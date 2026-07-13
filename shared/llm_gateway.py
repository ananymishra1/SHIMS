"""Resilient LLM gateway — the single choke point for SHIMS AI calls.

Every brain (copilot, chemistry, BMR corpus, governor, autonomous engine, …)
ends up here, either through :func:`shared.ai.ask_ai` (prompt/system surface,
never raises) or through :func:`LLMGateway.chat_messages` (messages/tools
surface used by the agent loop, raises :class:`LLMUnavailable`).

The gateway does not reimplement providers — it delegates to the existing
transports in :mod:`shared.ai` and :mod:`shared.agent_loop` and adds:

* per-provider circuit breaker (consecutive failures open the breaker),
* bounded retry on fast transient failures,
* global concurrency cap so parallel brains can't stampede Ollama,
* cached provider health for the UI,
* one ``ai_gateway_usage`` row per call for the usage dashboard
  (``ai_usage_log`` is the older per-user token-quota table — different thing).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from .config import settings, env_bool, env_int
from .database import db

GATEWAY_ENABLED = env_bool('SHIMS_LLM_GATEWAY', True)

_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN = 120.0
_HEALTH_TTL = 60.0
_RETRY_BACKOFF = 2.0
# Only retry failures that came back fast (connection refused etc.) — retrying
# a slow timeout would double an already painful wait.
_FAST_FAILURE_SECONDS = 10.0

_CLOUD_PROVIDERS = ('anthropic', 'openai', 'gemini', 'deepseek', 'kimi', 'qwen')


class LLMUnavailable(Exception):
    """Structured LLM failure for streaming/agent paths."""

    def __init__(self, code: str, provider: str = '', detail: str = '', retryable: bool = True) -> None:
        super().__init__(f"{provider or 'llm'}:{code} {detail}".strip())
        self.code = code
        self.provider = provider
        self.detail = detail
        self.retryable = retryable


# Feature keys every AI call site tags itself with. The admin routing table
# maps each one to a provider/model (+ fallback); unrouted features use the
# default chain.
FEATURE_KEYS = [
    ('copilot', 'Shims copilot — fast conversational turns'),
    ('copilot_deep', 'Shims copilot — deep analysis (CAPA, exec readouts)'),
    ('chemistry', 'Product chemistry & R&D brain'),
    ('documents', 'Document/SOP drafting'),
    ('bmr_drafting', 'BMR corpus drafts & answers'),
    ('router', 'Agent wave planning (small fast model)'),
    ('governor', 'Neural governor routing/arbitration'),
    ('autonomous', 'Autonomous engine background drafting'),
    ('general', 'Everything not otherwise routed'),
]


def ensure_gateway_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_gateway_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature TEXT NOT NULL DEFAULT 'general',
            provider TEXT NOT NULL,
            model TEXT,
            prompt_chars INTEGER NOT NULL DEFAULT 0,
            completion_chars INTEGER NOT NULL DEFAULT 0,
            latency_ms REAL NOT NULL DEFAULT 0,
            ok INTEGER NOT NULL DEFAULT 1,
            error_code TEXT,
            user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_feature_routes (
            feature_key TEXT PRIMARY KEY,
            provider TEXT,
            model TEXT,
            fallback_provider TEXT,
            fallback_model TEXT,
            updated_by INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Seed routes that preserve the models these features were hardcoded to
    # before routing existed. INSERT OR IGNORE keeps admin edits intact.
    for key, provider, model in (
        ('chemistry', 'ollama', 'qwen2.5:14b'),
        ('bmr_drafting', 'ollama', 'qwen2.5:14b'),
    ):
        try:
            db.execute('INSERT OR IGNORE INTO ai_feature_routes(feature_key, provider, model) VALUES (?, ?, ?)',
                       (key, provider, model))
        except Exception:
            pass


def get_feature_routes() -> dict[str, dict[str, Any]]:
    try:
        return {r['feature_key']: r for r in db.query('SELECT * FROM ai_feature_routes')}
    except Exception:
        return {}


def set_feature_route(feature_key: str, provider: str, model: str,
                      fallback_provider: str = '', fallback_model: str = '',
                      updated_by: int | None = None) -> None:
    ensure_gateway_schema()
    db.execute(
        'INSERT INTO ai_feature_routes(feature_key, provider, model, fallback_provider, fallback_model, updated_by, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) '
        'ON CONFLICT(feature_key) DO UPDATE SET provider=excluded.provider, model=excluded.model, '
        'fallback_provider=excluded.fallback_provider, fallback_model=excluded.fallback_model, '
        'updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP',
        (feature_key, provider or '', model or '', fallback_provider or '', fallback_model or '', updated_by),
    )


def resolve_route(feature: str) -> tuple[Optional[str], Optional[str]]:
    """Admin-configured (provider, model) for a feature, or (None, None)."""
    route = get_feature_routes().get(feature)
    if route and (route.get('provider') or route.get('model')):
        return route.get('provider') or None, route.get('model') or None
    return None, None


def _is_transient(error: str) -> bool:
    lowered = (error or '').lower()
    return any(token in lowered for token in ('timeout', 'timed out', 'connect', 'connection', 'temporarily', '502', '503', '529'))


def _cloud_configured(name: str) -> bool:
    from .ai import _stored_provider
    if _stored_provider(name):
        return True
    if name == 'anthropic':
        return bool(getattr(settings, 'anthropic_api_key', ''))
    if name == 'openai':
        return bool(settings.openai_api_key)
    if name == 'gemini':
        return bool(settings.google_api_key)
    import os
    return bool(os.getenv(f'{name.upper()}_API_KEY', ''))


class LLMGateway:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(env_int('SHIMS_LLM_MAX_CONCURRENT', 4))
        # provider -> {'fails': int, 'open_until': float}
        self._breaker: dict[str, dict[str, float]] = {}
        # provider -> {'checked': float, 'ok': bool, 'latency_ms': float, 'detail': str}
        self._health: dict[str, dict[str, Any]] = {}
        self._schema_ready = False

    # ── breaker ──────────────────────────────────────────────────────────
    def breaker_open(self, provider: str) -> bool:
        state = self._breaker.get(provider)
        return bool(state and state.get('open_until', 0) > time.time())

    def _record_provider_result(self, provider: str, ok: bool) -> None:
        state = self._breaker.setdefault(provider, {'fails': 0, 'open_until': 0.0})
        if ok:
            state['fails'] = 0
            state['open_until'] = 0.0
        else:
            state['fails'] += 1
            if state['fails'] >= _BREAKER_THRESHOLD:
                state['open_until'] = time.time() + _BREAKER_COOLDOWN

    # ── usage log ────────────────────────────────────────────────────────
    def record_usage(self, *, feature: str, provider: str, model: str, prompt_chars: int,
                     completion_chars: int, latency_ms: float, ok: bool,
                     error_code: str = '', user_id: int | None = None) -> None:
        try:
            if not self._schema_ready:
                ensure_gateway_schema()
                self._schema_ready = True
            db.execute(
                'INSERT INTO ai_gateway_usage(feature, provider, model, prompt_chars, completion_chars, latency_ms, ok, error_code, user_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (feature, provider, model or '', prompt_chars, completion_chars, round(latency_ms, 1),
                 1 if ok else 0, error_code or None, user_id),
            )
        except Exception:
            pass  # usage metering must never break an AI call

    # ── prompt/system surface (ask_ai) ───────────────────────────────────
    def _provider_chain(self, requested: Optional[str]) -> list[str]:
        first = (requested or settings.ai_provider or 'ollama').lower().strip()
        if first in {'google', 'claude'}:
            first = {'google': 'gemini', 'claude': 'anthropic'}[first]
        chain = [first]
        # Add LM Studio as second priority if it's not the first and is running
        if first != 'lmstudio':
            chain.append('lmstudio')
        if first != 'ollama':
            chain.append('ollama')
        for name in _CLOUD_PROVIDERS:
            if name not in chain and _cloud_configured(name):
                chain.append(name)
        return chain

    async def complete(self, prompt: str, system: str = '', *, feature: str = 'general',
                       provider: Optional[str] = None, model: Optional[str] = None,
                       user_id: int | None = None) -> Any:
        """ask_ai surface: always returns an AIResult, never raises."""
        from .ai import get_provider

        # Per-feature admin routing applies when the caller didn't pin a model.
        if not model:
            route_provider, route_model = resolve_route(feature)
            if route_model:
                provider = route_provider or provider
                model = route_model

        chain = self._provider_chain(provider)
        last_result: Any = None
        for idx, name in enumerate(chain):
            if self.breaker_open(name):
                continue
            # Only the explicitly requested provider gets the explicit model.
            use_model = model if idx == 0 else None
            attempts = 0
            while attempts < 2:
                attempts += 1
                start = time.time()
                try:
                    async with self._sem:
                        result = await get_provider(name).complete(prompt=prompt, system=system, model=use_model)
                except Exception as exc:  # providers shouldn't raise, but never trust that
                    elapsed = time.time() - start
                    self._record_provider_result(name, False)
                    self.record_usage(feature=feature, provider=name, model=use_model or '',
                                      prompt_chars=len(prompt), completion_chars=0,
                                      latency_ms=elapsed * 1000, ok=False,
                                      error_code=str(exc)[:80], user_id=user_id)
                    if _is_transient(str(exc)) and elapsed < _FAST_FAILURE_SECONDS and attempts < 2:
                        await asyncio.sleep(_RETRY_BACKOFF)
                        continue
                    break
                elapsed = time.time() - start
                ok = bool(getattr(result, 'ok', True)) and not str(getattr(result, 'route', '')).endswith(':fallback')
                self._record_provider_result(name, ok)
                self.record_usage(feature=feature, provider=name, model=getattr(result, 'model', use_model or ''),
                                  prompt_chars=len(prompt), completion_chars=len(getattr(result, 'text', '') or ''),
                                  latency_ms=elapsed * 1000, ok=ok,
                                  error_code='' if ok else str(getattr(result, 'error', ''))[:80], user_id=user_id)
                if ok:
                    return result
                last_result = result
                if _is_transient(str(getattr(result, 'error', ''))) and elapsed < _FAST_FAILURE_SECONDS and attempts < 2:
                    await asyncio.sleep(_RETRY_BACKOFF)
                    continue
                break
        if last_result is not None:
            return last_result
        from .ai import FallbackProvider
        return await FallbackProvider().complete(prompt, system, model=model)

    # ── messages/tools surface (agent loop) ──────────────────────────────
    async def chat_messages(self, provider: str, model: str, messages: list[dict[str, Any]],
                            tools: list[dict[str, Any]], *, feature: str = 'agent',
                            timeout: float = 120.0, user_id: int | None = None) -> dict[str, Any]:
        """Agent-loop surface: returns {content, tool_calls}; raises LLMUnavailable."""
        from . import agent_loop

        name = (provider or 'ollama').lower().strip()
        if self.breaker_open(name):
            raise LLMUnavailable('circuit_open', provider=name,
                                 detail=f'{name} skipped for {int(_BREAKER_COOLDOWN)}s after repeated failures')
        prompt_chars = sum(len(str(m.get('content', ''))) for m in messages)
        attempts = 0
        while True:
            attempts += 1
            start = time.time()
            try:
                async with self._sem:
                    if name == 'anthropic':
                        result = await agent_loop._anthropic_chat_stream_raw(model, messages, tools, timeout=timeout)
                    elif name in {'openai', 'kimi', 'deepseek', 'qwen', 'lmstudio'}:
                        result = await agent_loop._openai_compatible_chat_raw(name, model, messages, tools, timeout=timeout)
                    elif name == 'huggingface':
                        result = await agent_loop._hf_chat_raw(model, messages, tools, timeout=timeout)
                    else:
                        result = await agent_loop._ollama_chat_raw(model, messages, tools, timeout=timeout)
                elapsed = time.time() - start
                self._record_provider_result(name, True)
                self.record_usage(feature=feature, provider=name, model=model, prompt_chars=prompt_chars,
                                  completion_chars=len(result.get('content', '') or ''),
                                  latency_ms=elapsed * 1000, ok=True, user_id=user_id)
                return result
            except LLMUnavailable:
                raise
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                elapsed = time.time() - start
                self._record_provider_result(name, False)
                code = 'timeout' if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)) else \
                    'unreachable' if isinstance(exc, httpx.ConnectError) else 'http_error'
                self.record_usage(feature=feature, provider=name, model=model, prompt_chars=prompt_chars,
                                  completion_chars=0, latency_ms=elapsed * 1000, ok=False,
                                  error_code=code, user_id=user_id)
                if code != 'timeout' and elapsed < _FAST_FAILURE_SECONDS and attempts < 2:
                    await asyncio.sleep(_RETRY_BACKOFF)
                    continue
                raise LLMUnavailable(code, provider=name, detail=str(exc)[:200]) from exc
            except Exception as exc:
                elapsed = time.time() - start
                self._record_provider_result(name, False)
                self.record_usage(feature=feature, provider=name, model=model, prompt_chars=prompt_chars,
                                  completion_chars=0, latency_ms=elapsed * 1000, ok=False,
                                  error_code=str(exc)[:80], user_id=user_id)
                raise LLMUnavailable('error', provider=name, detail=str(exc)[:200]) from exc

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> dict[str, Any]:
        now = time.time()
        providers: dict[str, Any] = {}

        cached = self._health.get('ollama')
        if not cached or now - cached['checked'] > _HEALTH_TTL:
            start = time.time()
            entry: dict[str, Any] = {'checked': now, 'ok': False, 'latency_ms': 0.0, 'detail': '', 'models': 0}
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
                    r.raise_for_status()
                    models = r.json().get('models', [])
                entry.update(ok=True, latency_ms=round((time.time() - start) * 1000, 1), models=len(models))
            except Exception as exc:
                entry['detail'] = str(exc)[:120]
            self._health['ollama'] = entry
            cached = entry
        providers['ollama'] = {
            'configured': True,
            'ok': cached['ok'],
            'latency_ms': cached.get('latency_ms', 0),
            'models': cached.get('models', 0),
            'breaker_open': self.breaker_open('ollama'),
            'detail': cached.get('detail', ''),
        }

        # Hugging Face local endpoint health check
        cached_hf = self._health.get('huggingface')
        if not cached_hf or now - cached_hf['checked'] > _HEALTH_TTL:
            start = time.time()
            entry_hf: dict[str, Any] = {'checked': now, 'ok': False, 'latency_ms': 0.0, 'detail': '', 'models': 0}
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(f"{settings.huggingface_base_url.rstrip('/')}/v1/models")
                    r.raise_for_status()
                    models = r.json().get('data', [])
                entry_hf.update(ok=True, latency_ms=round((time.time() - start) * 1000, 1), models=len(models))
            except Exception as exc:
                entry_hf['detail'] = str(exc)[:120]
            self._health['huggingface'] = entry_hf
            cached_hf = entry_hf
        providers['huggingface'] = {
            'configured': True,
            'ok': cached_hf['ok'],
            'latency_ms': cached_hf.get('latency_ms', 0),
            'models': cached_hf.get('models', 0),
            'breaker_open': self.breaker_open('huggingface'),
            'detail': cached_hf.get('detail', ''),
        }

        # LM Studio health check
        cached_lm = self._health.get('lmstudio')
        if not cached_lm or now - cached_lm['checked'] > _HEALTH_TTL:
            start = time.time()
            entry_lm: dict[str, Any] = {'checked': now, 'ok': False, 'latency_ms': 0.0, 'detail': '', 'models': 0}
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(f"{settings.lmstudio_base_url.rstrip('/')}/v1/models")
                    r.raise_for_status()
                    models = r.json().get('data', [])
                entry_lm.update(ok=True, latency_ms=round((time.time() - start) * 1000, 1), models=len(models))
            except Exception as exc:
                entry_lm['detail'] = str(exc)[:120]
            self._health['lmstudio'] = entry_lm
            cached_lm = entry_lm
        providers['lmstudio'] = {
            'configured': True,
            'ok': cached_lm['ok'],
            'latency_ms': cached_lm.get('latency_ms', 0),
            'models': cached_lm.get('models', 0),
            'breaker_open': self.breaker_open('lmstudio'),
            'detail': cached_lm.get('detail', ''),
        }

        for name in _CLOUD_PROVIDERS:
            configured = _cloud_configured(name)
            providers[name] = {
                'configured': configured,
                'ok': configured and not self.breaker_open(name),
                'breaker_open': self.breaker_open(name),
            }

        usable = providers['ollama']['ok'] or providers['huggingface']['ok'] or providers['lmstudio']['ok'] or any(
            p['configured'] and not p['breaker_open'] for n, p in providers.items() if n not in ('ollama', 'huggingface', 'lmstudio')
        )
        return {'ok': usable, 'providers': providers, 'checked_at': now}

    def best_cloud_chat_provider(self) -> Optional[tuple[str, str]]:
        """Cloud (provider, model) usable by the agent loop when Ollama is down.

        The agent loop's cloud transport is Anthropic-only, so only anthropic
        qualifies here.
        """
        if _cloud_configured('anthropic') and not self.breaker_open('anthropic'):
            from .ai import _stored_provider
            stored = _stored_provider('anthropic') or {}
            model = stored.get('default_model') or getattr(settings, 'anthropic_model', 'claude-sonnet-4-6')
            return 'anthropic', model
        return None


gateway = LLMGateway()
