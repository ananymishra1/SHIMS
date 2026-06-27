from __future__ import annotations

import json
import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet

from .config import settings
from .database import db
from .provider_registry import clean_secret
from .kimi_model_helper import normalize_kimi_model, kimi_fallback_chain


@dataclass
class AIResult:
    text: str
    provider: str
    model: str = ''
    ok: bool = True
    error: str = ''
    route: str = ''
    raw: Optional[Any] = None


class AIProvider:
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        raise NotImplementedError


def _decrypt_stored_secret(token: str) -> str:
    try:
        raw = hashlib.sha256(str(settings.secret_key).encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(raw)).decrypt(str(token).encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _stored_provider(provider: str) -> dict[str, Any] | None:
    try:
        row = db.one("SELECT * FROM enterprise_ai_provider_keys WHERE provider=? AND enabled=1", (provider,))
    except Exception:
        return None
    if not row:
        return None
    data = dict(row)
    data["api_key"] = clean_secret(_decrypt_stored_secret(data.get("encrypted_key") or ""))
    return data if data["api_key"] else None


class FallbackProvider(AIProvider):
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        lower = prompt.lower()
        if 'experiment' in lower or 'r&d' in lower or 'research' in lower:
            text = (
                'R&D suggestion: define the objective, lock the control batch, run a small DOE, '
                'capture raw observations, send samples to QC, and only scale after repeatability is confirmed.'
            )
        elif 'coa' in lower or 'quality' in lower or 'qc' in lower:
            text = (
                'QC suggestion: verify identity, assay, physical description, moisture/LOD, pH where applicable, '
                'microbial status if required, instrument calibration, analyst sign-off, and final QA review.'
            )
        elif 'warehouse' in lower or 'stock' in lower or 'inventory' in lower:
            text = 'Warehouse suggestion: reconcile physical stock, log inward/outward movement, check min-stock alerts, and link low stock to procurement.'
        elif 'production' in lower or 'batch' in lower:
            text = 'Production suggestion: confirm approved material availability, line clearance, batch documents, in-process checks, QC sampling, and blocker closure.'
        elif 'procurement' in lower or 'purchase' in lower:
            text = 'Procurement suggestion: validate vendor approval, compare lead time, check stock coverage, and route high-value requests for approval.'
        else:
            text = (
                'I am running in local fallback mode. Configure Ollama, OpenAI, or Gemini in .env for deeper model responses. '
                'I can still route enterprise tasks, generate documents, run safe code tests, and manage local records.'
            )
        return AIResult(text=text, provider='fallback', model=model or 'fallback', route='fallback')


class OllamaProvider(AIProvider):
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        payload = {
            'model': model or settings.ollama_model,
            'messages': [
                {'role': 'system', 'content': system or 'You are SHIMS, a careful local-first AI operating system.'},
                {'role': 'user', 'content': prompt},
            ],
            'stream': False,
        }
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                res = await client.post(f'{settings.ollama_base_url.rstrip("/")}/api/chat', json=payload)
                res.raise_for_status()
                data = res.json()
            text = data.get('message', {}).get('content') or data.get('response') or ''
            used_model = data.get('model') or payload['model']
            return AIResult(text=text.strip() or 'Ollama returned an empty response.', provider='ollama', model=used_model, route='ollama', raw=data)
        except Exception as exc:
            fallback = await FallbackProvider().complete(prompt, system, tools, model=model)
            fallback.text = f'Ollama unavailable ({exc}). {fallback.text}'
            fallback.ok = False
            fallback.error = str(exc)
            fallback.route = 'ollama:fallback'
            return fallback


class OpenAIProvider(AIProvider):
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        stored = _stored_provider('openai')
        api_key = clean_secret((stored or {}).get('api_key') or settings.openai_api_key)
        if not api_key:
            return await FallbackProvider().complete(prompt, system, tools, model=model)
        payload = {
            'model': model or (stored or {}).get('default_model') or settings.openai_model,
            'input': [
                {'role': 'system', 'content': system or 'You are SHIMS.'},
                {'role': 'user', 'content': prompt},
            ],
            'max_output_tokens': 16000,
        }
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        base_url = ((stored or {}).get('base_url') or 'https://api.openai.com/v1').rstrip('/')
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(f'{base_url}/responses', json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
            text = data.get('output_text')
            if not text:
                chunks: list[str] = []
                for item in data.get('output', []):
                    for content in item.get('content', []):
                        if content.get('type') in {'output_text', 'text'}:
                            chunks.append(content.get('text', ''))
                text = '\n'.join(chunks)
            return AIResult(text=(text or '').strip(), provider='openai', model=payload['model'], route='openai', raw=data)
        except Exception as exc:
            fallback = await FallbackProvider().complete(prompt, system, tools, model=model)
            fallback.text = f'OpenAI unavailable ({exc}). {fallback.text}'
            fallback.ok = False
            fallback.error = str(exc)
            fallback.route = 'openai:fallback'
            return fallback


class GeminiProvider(AIProvider):
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        stored = _stored_provider('gemini')
        api_key = clean_secret((stored or {}).get('api_key') or settings.google_api_key)
        if not api_key:
            return await FallbackProvider().complete(prompt, system, tools, model=model)
        used_model = model or (stored or {}).get('default_model') or settings.gemini_model
        base_url = ((stored or {}).get('base_url') or 'https://generativelanguage.googleapis.com/v1beta').rstrip('/')
        url = f'{base_url}/models/{used_model}:generateContent?key={api_key}'
        payload = {
            'systemInstruction': {'parts': [{'text': system or 'You are SHIMS.'}]},
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(url, json=payload)
                res.raise_for_status()
                data = res.json()
            parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
            text = ''.join(p.get('text', '') for p in parts)
            return AIResult(text=text.strip(), provider='gemini', model=used_model, route='gemini', raw=data)
        except Exception as exc:
            fallback = await FallbackProvider().complete(prompt, system, tools, model=model)
            fallback.text = f'Gemini unavailable ({exc}). {fallback.text}'
            fallback.ok = False
            fallback.error = str(exc)
            fallback.route = 'gemini:fallback'
            return fallback



class AnthropicProvider(AIProvider):
    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        stored = _stored_provider('anthropic')
        api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'anthropic_api_key', '') or '')
        if not api_key:
            return await FallbackProvider().complete(prompt, system, tools, model=model)
        used_model = model or (stored or {}).get('default_model') or getattr(settings, 'anthropic_model', 'claude-sonnet-4-6')
        payload = {'model': used_model, 'max_tokens': 8192, 'system': system or 'You are SHIMS.', 'messages': [{'role': 'user', 'content': prompt}]}
        headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
        base_url = ((stored or {}).get('base_url') or 'https://api.anthropic.com/v1').rstrip('/')
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                res = await client.post(f'{base_url}/messages', json=payload, headers=headers)
                res.raise_for_status(); data = res.json()
            text = ''.join(part.get('text', '') for part in data.get('content', []) if isinstance(part, dict))
            return AIResult(text=text.strip(), provider='anthropic', model=used_model, route='anthropic', raw=data)
        except Exception as exc:
            fallback = await FallbackProvider().complete(prompt, system, tools, model=model)
            fallback.text = f'Anthropic unavailable ({exc}). {fallback.text}'
            fallback.ok = False
            fallback.error = str(exc)
            fallback.route = 'anthropic:fallback'
            return fallback


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, provider_name: str, default_base_url: str, default_model: str, env_key: str = '') -> None:
        self.provider_name = provider_name
        self.default_base_url = default_base_url
        self.default_model = default_model
        self.env_key = env_key

    async def complete(self, prompt: str, system: str = '', tools: Optional[list[dict[str, Any]]] = None, model: Optional[str] = None) -> AIResult:
        import os
        stored = _stored_provider(self.provider_name)
        api_key = clean_secret((stored or {}).get('api_key') or (os.getenv(self.env_key) if self.env_key else ''))
        local_no_key = self.provider_name == 'chemdfm'
        if not api_key and not local_no_key:
            return await FallbackProvider().complete(prompt, system, tools, model=model)
        raw_model = model or (stored or {}).get('default_model') or self.default_model
        # Normalize Kimi model names (e.g. "k2.6" → "kimi-k2.6")
        used_model = normalize_kimi_model(raw_model) if self.provider_name == 'kimi' else raw_model
        base_url = ((stored or {}).get('base_url') or self.default_base_url).rstrip('/')

        # Build candidate list: for Kimi, try fallback chain on 404.
        candidates = [used_model]
        if self.provider_name == 'kimi':
            candidates = kimi_fallback_chain(used_model)

        last_error = ""
        for attempt_model in candidates:
            payload = {
                'model': attempt_model,
                'messages': [
                    {'role': 'system', 'content': system or 'You are SHIMS.'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': 0.2,
            }
            # Kimi K2.x models only accept temperature=1.
            if self.provider_name == 'kimi' and isinstance(attempt_model, str) and attempt_model.startswith('kimi-k2'):
                payload['temperature'] = 1
            headers = {'Content-Type': 'application/json'}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    res = await client.post(f'{base_url}/chat/completions', json=payload, headers=headers)
                    res.raise_for_status()
                    data = res.json()
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                return AIResult(
                    text=(text or '').strip(),
                    provider=self.provider_name,
                    model=attempt_model,
                    route=f'{self.provider_name}:openai-compatible',
                    raw=data,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404 and self.provider_name == 'kimi':
                    last_error = f"Kimi model `{attempt_model}` not found (404)."
                    continue  # try next fallback
                # Non-404 or non-Kimi: fall through to general fallback
                last_error = str(exc)
                break
            except Exception as exc:
                last_error = str(exc)
                break

        # All candidates exhausted or non-retryable error.
        fallback = await FallbackProvider().complete(prompt, system, tools, model=model)
        fallback.text = f'{self.provider_name} unavailable ({last_error}). {fallback.text}'
        fallback.ok = False
        fallback.error = last_error
        fallback.route = f'{self.provider_name}:fallback'
        return fallback

def get_provider(name: Optional[str] = None) -> AIProvider:
    provider = (name or settings.ai_provider or 'ollama').lower().strip()
    if provider == 'openai':
        return OpenAIProvider()
    if provider in {'gemini', 'google'}:
        return GeminiProvider()
    if provider in {'anthropic', 'claude'}:
        return AnthropicProvider()
    if provider == 'kimi':
        from .kimi_model_helper import normalize_kimi_model
        return OpenAICompatibleProvider('kimi', settings.kimi_base_url, normalize_kimi_model(settings.kimi_model), 'KIMI_API_KEY')
    if provider == 'qwen':
        return OpenAICompatibleProvider('qwen', settings.qwen_base_url, settings.qwen_model, 'QWEN_API_KEY')
    if provider == 'deepseek':
        return OpenAICompatibleProvider('deepseek', 'https://api.deepseek.com/v1', 'deepseek-chat', 'DEEPSEEK_API_KEY')
    if provider == 'chemdfm':
        return OpenAICompatibleProvider('chemdfm', 'http://127.0.0.1:8000/v1', 'OpenDFM/ChemDFM-R-14B', 'CHEMDFM_API_KEY')
    if provider == 'fallback':
        return FallbackProvider()
    return OllamaProvider()


async def ask_ai(prompt: str, system: str = '', provider: Optional[str] = None, model: Optional[str] = None,
                 feature: str = 'general', user_id: Optional[int] = None) -> AIResult:
    from .llm_gateway import GATEWAY_ENABLED, gateway
    if GATEWAY_ENABLED:
        return await gateway.complete(prompt, system, feature=feature, provider=provider, model=model, user_id=user_id)
    return await get_provider(provider).complete(prompt=prompt, system=system, model=model)


def extract_json_maybe(text: str) -> Any:
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None
