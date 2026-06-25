"""Omni DuoBot — a shared conversation space where the primary SHIMS instance
and the Local Factory instance chat with each other, with the user able to
intervene authoritatively and approve/reject improvement proposals.

Storage:
  STORAGE_DIR/duobot/conversations.jsonl   — conversation metadata + messages
  STORAGE_DIR/duobot/proposal_votes.jsonl  — user approve/reject ledger
"""
from __future__ import annotations

import asyncio
import httpx
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR, settings
from .inter_instance_bridge import PeerClient, get_peer, quick_brain_status
from .security import new_id

try:
    from . import ai as ai_module
except Exception:  # pragma: no cover
    ai_module = None

try:
    from .self_evolver import apply_proposal, approve_proposal, list_proposals
except Exception:  # pragma: no cover
    def apply_proposal(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "self_evolver not available"}

    def approve_proposal(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "self_evolver not available"}

    def list_proposals(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

try:
    from .prompt_evolution import promote_variant
except Exception:  # pragma: no cover
    def promote_variant(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "prompt_evolution not available"}

DUOBOT_DIR = STORAGE_DIR / "duobot"
DUOBOT_DIR.mkdir(parents=True, exist_ok=True)
CONVERSATIONS_PATH = DUOBOT_DIR / "conversations.jsonl"
VOTES_PATH = DUOBOT_DIR / "proposal_votes.jsonl"
SETTINGS_PATH = DUOBOT_DIR / "settings.json"

MAX_MESSAGES = int(os.getenv("SHIMS_DUOBOT_MAX_MESSAGES", "100"))
DUPLICATE_LOOKBACK = int(os.getenv("SHIMS_DUOBOT_DUPLICATE_LOOKBACK", "3"))
CAPABILITY_CACHE_TTL = int(os.getenv("SHIMS_DUOBOT_CAPABILITY_TTL_SECONDS", "45"))
PRIMARY_TURN_TIMEOUT = float(os.getenv("SHIMS_DUOBOT_PRIMARY_TIMEOUT_SECONDS", "45"))
LOCAL_TURN_TIMEOUT = float(os.getenv("SHIMS_DUOBOT_LOCAL_TIMEOUT_SECONDS", "45"))
CAPABILITY_PEER_TIMEOUT = float(os.getenv("SHIMS_DUOBOT_CAPABILITY_PEER_TIMEOUT_SECONDS", "8"))

COUNCIL_MODES = {"free", "improvement", "council"}

COUNCIL_PERSONAS: dict[str, dict[str, Any]] = {
    "primary": {
        "name": "Omni",
        "provider": "primary",
        "model": "",
        "description": "The primary SHIMS instance — pragmatic, user-aligned, knows the full system. Acts as a generalist advisor for any user task, not only SHIMS improvement.",
    },
    "local": {
        "name": "Factory",
        "provider": "ollama",
        "model": "",
        "description": "The offline Local Factory — privacy-first, cost-aware, CPU/GPU local. Keeps answers grounded in what can run without cloud access.",
    },
    "gemini": {
        "name": "Gemini",
        "provider": "google",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "description": "Google Gemini — thorough, multimodal, ecosystem-aware. Good at big-picture architecture, alternatives, and risk scanning for any domain.",
    },
    "anthropic": {
        "name": "Claude",
        "provider": "anthropic",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-6",
        "description": "Anthropic Claude — safety-focused, careful, principled. Questions risky changes and verifies edge cases, whether the topic is SHIMS or general.",
    },
    "openai": {
        "name": "OpenAI",
        "provider": "openai",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "description": "OpenAI — strong tooling, coding, and desktop automation reasoning. Drafts concrete implementation steps with file paths and tool calls when relevant.",
    },
}

CHEMISTRY_TERMS = {
    "chemistry", "chemical", "reaction", "synthesis", "retrosynthesis", "molecule",
    "smiles", "solvent", "impurity", "assay", "hazard", "chemdfm", "api",
    "intermediate", "stoichiometry", "yield", "purity", "bmr", "route",
}
MANUFACTURING_TERMS = {
    "industrial", "manufacturing", "factory", "plant", "production", "mes",
    "gmp", "qms", "lims", "qc", "qa", "batch", "scale-up", "scale up",
    "material balance", "raw material", "procurement", "warehouse", "ehs",
    "effluent", "deviation", "capa", "line clearance", "tech transfer",
}
LATENCY_TERMS = {"latency", "fast", "voice", "realtime", "real-time", "chat", "streaming", "slow"}


def _legacy_system_prompt(role: str, mode: str) -> str:
    """Return a system prompt tailored to the speaker and chat mode."""
    mode = (mode or "free").lower()
    base_prefix = "Speak directly in your own voice. Do NOT start your reply with 'Factory:', 'Omni:', or any speaker label. "
    if mode == "improvement":
        if role == "primary":
            return (
                "You are SHIMS Omni (the primary cloud-connected instance). "
                + base_prefix +
                "You are collaborating with your local factory twin in a private chat. "
                "Discuss concrete improvements to SHIMS — code, prompts, skills, tests, or features. "
                "Be concise, technical, and end each message with one actionable proposal if you have one. "
                "Do not repeat proposals or points already made."
            )
        return (
            "You are SHIMS Local Factory (the offline Ollama-powered instance). "
            + base_prefix +
            "You are collaborating with the primary Omni instance in a private chat. "
            "Discuss concrete improvements to SHIMS from a local-first, CPU-friendly perspective. "
            "Be concise and end with one actionable proposal if you have one. "
            "Do not repeat proposals or points already made."
        )
    # Default free-chat mode.
    if role == "primary":
        return (
            "You are SHIMS Omni (the primary cloud-connected instance). "
            + base_prefix +
            "You are having a free-form conversation with your local factory twin. "
            "Answer naturally, stay helpful, and be concise. You may discuss any topic the user sets. "
            "IMPORTANT: Do not just thank or summarize the other speaker. Add a fresh insight, question, example, or challenge."
        )
    return (
        "You are SHIMS Local Factory (the offline Ollama-powered instance). "
        + base_prefix +
        "You are having a free-form conversation with the primary Omni instance. "
        "Answer naturally, stay helpful, and be concise. You may discuss any topic the user sets. "
        "IMPORTANT: Do not just thank or summarize the other speaker. Add a fresh insight, question, example, or challenge."
    )


def _domain_profile(conv: dict[str, Any]) -> dict[str, Any]:
    """Classify a DuoBot thread so each side picks the right brain."""
    text_parts = [str(conv.get("topic") or ""), str(conv.get("mode") or "")]
    for msg in conv.get("messages", [])[-8:]:
        text_parts.append(str(msg.get("content") or ""))
    hay = " ".join(text_parts).lower()
    chem_hits = [term for term in CHEMISTRY_TERMS if term in hay]
    manufacturing_hits = [term for term in MANUFACTURING_TERMS if term in hay]
    latency_hits = [term for term in LATENCY_TERMS if term in hay]
    focus: list[str] = []
    if manufacturing_hits:
        focus.append("industrial manufacturing")
    if chem_hits:
        focus.append("chemistry")
    if latency_hits:
        focus.append("latency")
    if not focus and conv.get("mode") == "improvement":
        focus.append("SHIMS improvement")
    return {
        "focus": focus or ["general"],
        "chemistry": bool(chem_hits),
        "manufacturing": bool(manufacturing_hits),
        "latency": bool(latency_hits),
        "chemistry_hits": chem_hits,
        "manufacturing_hits": manufacturing_hits,
        "latency_hits": latency_hits,
        "role": "chemistry" if chem_hits and not latency_hits else ("heavy" if manufacturing_hits else "fast"),
    }


def _domain_prompt(domain: dict[str, Any]) -> str:
    focus = ", ".join(domain.get("focus") or ["general"])
    lines = [f"Current focus: {focus}."]
    if domain.get("manufacturing") or domain.get("chemistry"):
        lines.append(
            "For industrial manufacturing and chemistry, reason in operational terms: product route, raw-material ledger, "
            "unit-aware material balance, scale-up risk, QC/LIMS, MES, EHS/waste, procurement, QA deviation/CAPA, and BMR evidence."
        )
        lines.append(
            "Do not invent validated chemical facts. Mark assumptions, prefer deterministic SHIMS data when available, "
            "and suggest ChemDFM/R&D/BMR verification for chemistry-specific claims."
        )
    elif domain.get("focus") == ["general"] or (not domain.get("manufacturing") and not domain.get("chemistry") and not domain.get("latency")):
        lines.append(
            "No special domain focus. Answer the user's question directly. If they want a plan or workflow, outline concrete, actionable steps. "
            "Only route to SHIMS-specific tools when the request clearly involves SHIMS code, files, or desktop/server actions."
        )
    if domain.get("latency"):
        lines.append("Keep replies short enough for voice and realtime chat unless the user asks for a deep audit.")
    return " ".join(lines)


def _capability_prompt(capabilities: dict[str, Any] | None) -> str:
    if not capabilities:
        return ""
    primary = capabilities.get("primary") or {}
    local = capabilities.get("local") or {}
    local_caps = (local.get("capabilities") or {}) if isinstance(local, dict) else {}
    role_models = (local.get("role_models") or {}) if isinstance(local, dict) else {}
    active_caps = ", ".join(k for k, v in local_caps.items() if v) or "unknown"
    return (
        "Capability snapshot: "
        f"Primary provider/model: {primary.get('provider', settings.ai_provider)}/{primary.get('model', '')}. "
        f"Local factory capabilities: {active_caps}. Role models: {role_models}. "
        "Use the strongest available specialist only when it improves the answer."
    )


def _system_prompt(
    role: str,
    mode: str,
    domain: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> str:
    """Return a system prompt tailored to the speaker, mode, domain, and peers."""
    mode = (mode or "free").lower()
    domain = domain or {"focus": ["general"]}
    base_prefix = "Speak directly in your own voice. Do NOT start your reply with 'Factory:', 'Omni:', or any speaker label. "
    suffix = "\n\n" + _domain_prompt(domain)
    cap = _capability_prompt(capabilities)
    if cap:
        suffix += "\n" + cap
    if mode == "improvement":
        if role == "primary":
            return (
                "You are SHIMS Omni (the primary cloud-connected instance). "
                + base_prefix +
                "You are collaborating with your local factory twin in a private chat. "
                "Discuss concrete improvements to SHIMS - code, prompts, skills, tests, or features. "
                "Be concise, technical, and end each message with one actionable proposal if you have one. "
                "Do not repeat proposals or points already made."
                + suffix
            )
        return (
            "You are SHIMS Local Factory (the offline Ollama-powered instance). "
            + base_prefix +
            "You are collaborating with the primary Omni instance in a private chat. "
            "Discuss concrete improvements to SHIMS from a local-first, CPU-friendly perspective. "
            "Be concise and end with one actionable proposal if you have one. "
            "Do not repeat proposals or points already made."
            + suffix
        )
    if role == "primary":
        return (
            "You are SHIMS Omni (the primary cloud-connected instance). "
            + base_prefix +
            "You are having a free-form conversation with your local factory twin. "
            "Answer naturally, stay helpful, and be concise. You may discuss any topic the user sets. "
            "IMPORTANT: Do not just thank or summarize the other speaker. Add a fresh insight, question, example, or challenge."
            + suffix
        )
    return (
        "You are SHIMS Local Factory (the offline Ollama-powered instance). "
        + base_prefix +
        "You are having a free-form conversation with the primary Omni instance. "
        "Answer naturally, stay helpful, and be concise. You may discuss any topic the user sets. "
        "IMPORTANT: Do not just thank or summarize the other speaker. Add a fresh insight, question, example, or challenge."
        + suffix
    )


def _now() -> float:
    return time.time()


def _default_settings() -> dict[str, Any]:
    """Default AI settings for DuoBot. Primary uses global cloud provider; local uses Ollama default."""
    provider = settings.ai_provider or "ollama"
    model = ""
    if provider == "kimi":
        model = settings.kimi_model or "kimi-k2.6"
    elif provider == "openai":
        model = settings.openai_model or "gpt-4o-mini"
    elif provider == "anthropic":
        model = settings.anthropic_model or "claude-sonnet-4-6"
    elif provider == "huggingface":
        model = settings.huggingface_model or os.getenv("HUGGINGFACE_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    return {
        "primary_provider": provider,
        "primary_model": model,
        "local_model": os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", "qwen2.5:3b"),
        "local_temperature": 0.6,
        "council_auto_execute": os.getenv("SHIMS_DUOBOT_COUNCIL_AUTO_EXECUTE", "false").lower() in {"1", "true", "yes", "on"},
        "council_members": ["primary", "gemini", "anthropic", "openai", "local"],
        "council_chair": "primary",
        "council_rag_enabled": True,
        "council_rag_limit": 4,
        "council_personas": {mid: {"enabled": True, "provider": p["provider"], "model": "", "temperature": 0.6, "system_prompt": ""} for mid, p in COUNCIL_PERSONAS.items()},
    }


def _raw_settings() -> dict[str, Any]:
    """Return only user-saved settings, without merging defaults."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_settings() -> dict[str, Any]:
    defaults = _default_settings()
    defaults.update(_raw_settings())
    return defaults


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    allowed = {
        "primary_provider", "primary_model", "local_model", "local_temperature",
        "council_auto_execute", "council_members", "council_chair",
        "council_rag_enabled", "council_rag_limit", "council_personas",
    }
    current.update({k: v for k, v in updates.items() if k in allowed})
    SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _word_set(text: str) -> set[str]:
    return set(_normalized(text).split())


def _similarity(a: str, b: str) -> float:
    """Simple Jaccard word-overlap similarity (0–1)."""
    sa, sb = _word_set(a), _word_set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from model output."""
    import re as _re
    match = _re.search(r"```json\s*(.*?)\s*```", text, _re.S)
    if match:
        text = match.group(1)
    match = _re.search(r"\{.*\}", text, _re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        return None


def _is_duplicate(conv: dict[str, Any], content: str, role: str) -> bool:
    """Return True if the same or highly similar message was recently posted."""
    norm = _normalized(content)
    msgs = conv.get("messages", [])
    # Exact match within lookback.
    for prev in msgs[-DUPLICATE_LOOKBACK:]:
        if prev.get("role") != role:
            continue
        if _normalized(prev.get("content", "")) == norm:
            return True
    # Cross-role exact echo.
    if msgs and _normalized(msgs[-1].get("content", "")) == norm:
        return True
    # Semantic near-duplicate check against the last few messages.
    for prev in msgs[-5:]:
        if _similarity(content, prev.get("content", "")) >= 0.72:
            return True
    return False


def _jsonl_append(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def _jsonl_read(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
            if limit and len(items) >= limit:
                break
    return items


def _rewrite_conversations(convs: dict[str, dict[str, Any]]) -> None:
    CONVERSATIONS_PATH.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False, default=str) for c in convs.values()),
        encoding="utf-8",
    )
    if CONVERSATIONS_PATH.stat().st_size > 0:
        with CONVERSATIONS_PATH.open("a", encoding="utf-8") as f:
            f.write("\n")


def _default_council_settings() -> dict[str, Any]:
    defaults = _default_settings()
    return {
        "auto_execute": defaults.get("council_auto_execute", False),
        "members": defaults.get("council_members", ["primary", "gemini", "anthropic", "openai", "local"]),
        "chair": defaults.get("council_chair", "primary"),
        "rag_enabled": defaults.get("council_rag_enabled", True),
        "rag_limit": defaults.get("council_rag_limit", 4),
        "personas": defaults.get("council_personas", {}),
    }


def _build_personas(settings_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the active council persona list from defaults and user overrides."""
    members = settings_obj.get("members", ["primary", "gemini", "anthropic", "openai", "local"])
    persona_overrides = settings_obj.get("personas", {})
    personas: list[dict[str, Any]] = []
    for mid in members:
        base = COUNCIL_PERSONAS.get(mid)
        if not base:
            continue
        override = persona_overrides.get(mid, {})
        if override.get("enabled") is False:
            continue
        personas.append({
            "id": mid,
            "name": base["name"],
            "provider": override.get("provider") or base.get("provider"),
            "model": override.get("model", ""),
            "model_env": base.get("model_env"),
            "default_model": base.get("default_model"),
            "temperature": override.get("temperature", 0.6),
            "system_prompt": override.get("system_prompt", "").strip(),
            "description": base.get("description", ""),
        })
    return personas


def create_conversation(topic: str = "", mode: str = "free", created_by: str = "user") -> dict[str, Any]:
    mode = mode if mode in COUNCIL_MODES else "free"
    conv_id = new_id("duo")
    entry = {
        "id": conv_id,
        "topic": topic or ("SHIMS continuous improvement" if mode == "improvement" else "Council of the Wises" if mode == "council" else "Free chat"),
        "mode": mode,
        "created_at": _now(),
        "created_by": created_by,
        "status": "active",
        "messages": [],
    }
    if mode == "council":
        entry["council_settings"] = _default_council_settings()
        entry["personas"] = _build_personas(entry["council_settings"])
        entry["pending_council_actions"] = []
        entry["council_action_log"] = []
    if topic:
        entry["messages"].append({"role": "system", "content": f"Topic: {topic}", "ts": _now()})
    _jsonl_append(CONVERSATIONS_PATH, [entry])
    return {"ok": True, "conversation": entry}


def _load_all_conversations() -> dict[str, dict[str, Any]]:
    convs: dict[str, dict[str, Any]] = {}
    for entry in _jsonl_read(CONVERSATIONS_PATH):
        cid = entry.get("id")
        if cid:
            convs[cid] = entry
    return convs


def get_conversation(conv_id: str) -> dict[str, Any] | None:
    return _load_all_conversations().get(conv_id)


async def check_capabilities() -> dict[str, Any]:
    """Probe primary and local factory capabilities for routing and UI display."""
    started = time.perf_counter()
    primary_model = (
        settings.kimi_model if (settings.ai_provider or "").lower() == "kimi"
        else settings.ollama_model
    )
    primary: dict[str, Any] = {
        "ok": True,
        "instance_id": os.getenv("SHIMS_INSTANCE_ID", "primary"),
        "provider": settings.ai_provider,
        "model": primary_model,
        "storage": str(STORAGE_DIR),
    }
    primary["brain"] = quick_brain_status()

    peer = get_peer("local")
    local: dict[str, Any]
    if peer:
        peer_started = time.perf_counter()
        try:
            local = await asyncio.wait_for(PeerClient(peer).capabilities(), timeout=CAPABILITY_PEER_TIMEOUT)
        except asyncio.TimeoutError:
            local = {"ok": False, "error": f"local peer capability check timed out after {CAPABILITY_PEER_TIMEOUT:g}s"}
        local.setdefault("peer_url", peer.get("url"))
        local["roundtrip_ms"] = round((time.perf_counter() - peer_started) * 1000, 1)
    else:
        local = {"ok": False, "error": "local peer not configured"}

    result = {
        "ok": bool(primary.get("ok")) and bool(local.get("ok", True)),
        "primary": primary,
        "local": local,
        "dialogue_focus": [
            "industrial manufacturing",
            "chemistry",
            "brain/chat routing",
            "voice/chat latency",
        ],
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "checked_at": _now(),
    }
    return result


async def refresh_conversation_capabilities(conv_id: str, force: bool = False) -> dict[str, Any]:
    convs = _load_all_conversations()
    conv = convs.get(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    cached = conv.get("capabilities") or {}
    if not force and cached.get("checked_at") and _now() - float(cached.get("checked_at", 0)) < CAPABILITY_CACHE_TTL:
        return cached
    caps = await check_capabilities()
    conv["capabilities"] = caps
    conv["updated_at"] = _now()
    _rewrite_conversations(convs)
    return caps


def set_mode(conv_id: str, mode: str) -> dict[str, Any]:
    mode = mode if mode in COUNCIL_MODES else "free"
    convs = _load_all_conversations()
    conv = convs.get(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    conv["mode"] = mode
    conv["topic"] = conv.get("topic") or (
        "SHIMS continuous improvement" if mode == "improvement"
        else "Council of the Wises" if mode == "council"
        else "Free chat"
    )
    if mode == "council" and not conv.get("council_settings"):
        conv["council_settings"] = _default_council_settings()
        conv["personas"] = _build_personas(conv["council_settings"])
        conv["pending_council_actions"] = []
        conv["council_action_log"] = []
    _rewrite_conversations(convs)
    add_message(conv_id, "system", f"Mode switched to {mode}.")
    return {"ok": True, "conversation": get_conversation(conv_id)}


def list_conversations(limit: int = 20) -> list[dict[str, Any]]:
    convs = sorted(_load_all_conversations().values(), key=lambda x: x.get("created_at", 0), reverse=True)
    return convs[:limit]


def add_message(conv_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    convs = _load_all_conversations()
    conv = convs.get(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    msg = {"role": role, "content": content, "ts": _now(), "metadata": metadata or {}}
    conv["messages"].append(msg)
    conv["updated_at"] = _now()
    _rewrite_conversations(convs)
    return {"ok": True, "message": msg}


def _last_speaker(conv: dict[str, Any]) -> str:
    for msg in reversed(conv.get("messages", [])):
        if msg.get("role") in ("primary", "local"):
            return msg["role"]
    return "local"  # primary starts if local hasn't spoken


def _build_history(conv: dict[str, Any], speaker: str) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for msg in conv.get("messages", [])[-10:]:
        role = msg.get("role")
        if role == "user":
            history.append({"role": "user", "content": f"[User] {msg['content']}"})
        elif role == "primary":
            history.append({"role": "assistant" if speaker == "primary" else "user", "content": f"[Omni] {msg['content']}"})
        elif role == "local":
            history.append({"role": "assistant" if speaker == "local" else "user", "content": f"[Factory] {msg['content']}"})
    return history


def _call_ollama_chat_sync(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Direct Ollama chat call for the local-fallback path."""
    host = os.getenv("OLLAMA_BASE_URL", str(settings.ollama_base_url)).rstrip("/")
    url = f"{host}/api/chat"
    payload = {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.6}}
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return {"ok": True, "content": (data.get("message") or {}).get("content", "") or data.get("response", "")}
    except Exception as exc:
        return {"ok": False, "content": f"Ollama error: {exc}", "error": str(exc)}


def _build_primary_prompt(conv: dict[str, Any]) -> tuple[str, str]:
    """Build a plain-text prompt and system string for the primary (cloud) agent."""
    mode = conv.get("mode", "free")
    domain = _domain_profile(conv)
    system = _system_prompt("primary", mode, domain=domain, capabilities=conv.get("capabilities"))
    history_lines: list[str] = []
    for msg in conv.get("messages", [])[-10:]:
        role = msg.get("role")
        if role == "user":
            history_lines.append(f"User: {msg['content']}")
        elif role == "primary":
            history_lines.append(f"Omni: {msg['content']}")
        elif role == "local":
            history_lines.append(f"Factory: {msg['content']}")
    history = "\n\n".join(history_lines)
    prompt = f"Continue the conversation as Omni.\n\nDomain: {', '.join(domain.get('focus', []))}\n\n{history}\n\nOmni:"
    return system, prompt


async def _primary_say(conv: dict[str, Any]) -> dict[str, Any]:
    """Primary agent uses the user-selected cloud provider (Kimi by default); falls back to local Ollama."""
    mode = conv.get("mode", "free")
    system, prompt = _build_primary_prompt(conv)
    duo_settings = load_settings()
    provider = duo_settings.get("primary_provider") or settings.ai_provider or "ollama"
    model = duo_settings.get("primary_model", "")
    if provider == "kimi" and not model:
        model = settings.kimi_model or "kimi-k2.6"
    content = ""
    used_provider = provider
    used_model = model

    if ai_module:
        try:
            result = await asyncio.wait_for(
                ai_module.ask_ai(prompt, system=system, provider=provider, model=model),
                timeout=PRIMARY_TURN_TIMEOUT,
            )
            content = result.text or ""
            used_provider = result.provider or provider
            used_model = result.model or model
        except Exception as exc:
            content = ""
            used_provider = f"{provider}:error"
            used_model = str(exc)[:80]

    # Fallback to local Ollama if cloud did not produce usable text.
    if not content.strip():
        fallback_model = os.getenv("SHIMS_DUOBOT_FALLBACK_MODEL", "qwen2.5:3b")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        fb = await asyncio.to_thread(_call_ollama_chat_sync, fallback_model, messages)
        content = fb.get("content") or ""
        used_provider = "ollama"
        used_model = fallback_model

    if not content.strip():
        content = "I'm not sure what to add; let's keep chatting." if mode == "free" else "I don't have a concrete proposal right now; let's keep monitoring."

    return {"role": "primary", "content": content, "ts": _now(), "metadata": {"provider": used_provider, "model": used_model}}


def _local_model_for(
    domain: dict[str, Any],
    peer: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Choose the Local Factory model/role from the current domain, honoring user DuoBot settings."""
    user_local = _raw_settings().get("local_model", "")
    local = (capabilities or {}).get("local") or {}
    caps = local.get("capabilities") or {}
    role_models = local.get("role_models") or {}
    if user_local:
        # User has pinned a specific local model for DuoBot.
        return user_local, "user"
    if domain.get("chemistry") and not domain.get("latency"):
        if capabilities is not None:
            if caps.get("chemistry") and role_models.get("chemistry"):
                return os.getenv("SHIMS_FACTORY_CHEMISTRY_MODEL", role_models["chemistry"]), "chemistry"
            if caps.get("chemistry") is False:
                return os.getenv("SHIMS_FACTORY_HEAVY_MODEL", role_models.get("heavy") or (peer or {}).get("heavy_model", "qwen2.5:7b")), "heavy"
        return os.getenv("SHIMS_FACTORY_CHEMISTRY_MODEL", (peer or {}).get("chemistry_model", "chemdfm")), "chemistry"
    if domain.get("manufacturing"):
        return os.getenv("SHIMS_FACTORY_HEAVY_MODEL", role_models.get("heavy") or (peer or {}).get("heavy_model", "qwen2.5:7b")), "heavy"
    if domain.get("latency"):
        return os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", role_models.get("fast") or (peer or {}).get("default_model", "qwen2.5:3b")), "fast"
    return os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", role_models.get("fast") or (peer or {}).get("default_model", "qwen2.5:3b")), "fast"


async def _local_say(conv: dict[str, Any]) -> dict[str, Any]:
    """Local agent runs on the Local Factory instance via the peer bridge."""
    peer = get_peer("local")
    if not peer:
        return {"role": "local", "content": "Local peer not configured.", "ts": _now(), "metadata": {}}
    client = PeerClient(peer)
    mode = conv.get("mode", "free")
    domain = _domain_profile(conv)
    model, role = _local_model_for(domain, peer, conv.get("capabilities"))
    messages = [{"role": "system", "content": _system_prompt("local", mode, domain=domain, capabilities=conv.get("capabilities"))}] + _build_history(conv, "local")
    result = await client.chat_local(
        messages,
        model=model,
        role=role,
        temperature=0.2 if domain.get("manufacturing") or domain.get("chemistry") else 0.35,
        timeout=LOCAL_TURN_TIMEOUT,
    )
    if not result.get("ok"):
        # Older peers may not expose /api/peer/llm. Fall back to the whitelisted tool bridge.
        fallback = await client.call_tool("local_llm.chat", {"messages": messages, "role": role, "model": model, "temperature": 0.2})
        if not fallback.get("ok"):
            return {"role": "local", "content": f"Local factory error: {result.get('error') or fallback.get('error', 'unknown')}", "ts": _now(), "metadata": {"model": model, "role": role}}
        result = fallback.get("result") or {}
    content = result.get("content") or (result.get("result") or {}).get("content") or ""
    if not content.strip():
        content = "I don't have much to add right now." if mode == "free" else "No proposal from the factory side this turn."
    return {
        "role": "local",
        "content": content,
        "ts": _now(),
        "metadata": {
            "provider": result.get("provider", "ollama"),
            "model": result.get("model", model),
            "role": result.get("role", role),
            "route": result.get("route", "peer-llm"),
            "latency_ms": result.get("latency_ms"),
        },
    }


def _last_user_message(conv: dict[str, Any]) -> str:
    for msg in reversed(conv.get("messages", [])):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


async def _feed_council_context(conv_id: str) -> dict[str, Any]:
    """Retrieve SHIMS source context once per turn and add it as a system message.

    This keeps every council member chat-aware while giving them relevant long-term
    SHIMS context only when needed. Retrieval runs once (not per member) to save tokens.
    """
    try:
        from .omni_brain import retrieve_context
    except Exception as exc:
        return {"ok": False, "error": f"omni_brain not available: {exc}"}

    conv = get_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}

    settings_obj = conv.get("council_settings") or _default_council_settings()
    if not settings_obj.get("rag_enabled", True):
        return {"ok": True, "message": "RAG disabled"}

    query = _last_user_message(conv).strip()
    if len(query) < 10:
        return {"ok": True, "message": "query too short; no context retrieved"}

    limit = int(settings_obj.get("rag_limit", 4))
    try:
        ctx = await asyncio.wait_for(
            asyncio.to_thread(retrieve_context, query, limit=max(limit, 3)),
            timeout=float(os.getenv("SHIMS_DUOBOT_RAG_TIMEOUT_SECONDS", "5")),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}

    hits = [h for h in ctx.get("hits", []) if h.get("source") == "shims_source"][:limit]
    if not hits:
        return {"ok": True, "message": "no source hits", "hits": 0}

    lines = ["Relevant SHIMS source context (provided by Omni):"]
    for i, h in enumerate(hits, 1):
        title = h.get("title") or h.get("url") or "unknown"
        content = (h.get("content") or "").strip().replace("\n", " ")[:400]
        lines.append(f"[{i}] {title}\n{content}")
    context_text = "\n\n".join(lines)

    add_message(conv_id, "context", context_text, {"rag": True, "hits": len(hits)})
    return {"ok": True, "hits": len(hits)}


def _council_member_system(member: dict[str, Any], conv: dict[str, Any]) -> str:
    """System prompt for a council member."""
    custom = (member.get("system_prompt") or "").strip()
    if custom:
        return custom
    base = (
        "You are a member of the SHIMS Council of the Wises. "
        "Speak in your own voice. Do NOT start your reply with any speaker label. "
        "Be concise, technical, and constructive. "
        "You may disagree with other members, but stay focused on the user's request. "
        "The user may ask about ANY topic, plan, or use case — not only SHIMS self-improvement. "
        "Answer general questions directly. When SHIMS tools or source code can help, say so; otherwise give plain actionable advice."
    )
    persona = member.get("description", "")
    topic = conv.get("topic", "")
    return f"{base}\n\nPersona: {member.get('name', member['id'])} — {persona}\nTopic: {topic}"


def _council_history(conv: dict[str, Any]) -> list[dict[str, str]]:
    """Build OpenAI-style message history for council members."""
    history: list[dict[str, str]] = []
    for msg in conv.get("messages", [])[-25:]:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            history.append({"role": "user", "content": f"[User] {content}"})
        elif role in ("system", "context"):
            history.append({"role": "system", "content": content})
        elif role in ("primary", "local"):
            label = "Omni" if role == "primary" else "Factory"
            history.append({"role": "assistant", "content": f"[{label}] {content}"})
        elif role in COUNCIL_PERSONAS:
            history.append({"role": "assistant", "content": f"[{COUNCIL_PERSONAS[role]['name']}] {content}"})
        elif role == "chair":
            history.append({"role": "assistant", "content": f"[Chair] {content}"})
    return history


def _resolve_member_model(member: dict[str, Any]) -> tuple[str, str]:
    """Return (provider, model) for a council member, honoring per-member overrides."""
    mid = member["id"]
    override_provider = (member.get("provider") or "").strip()
    override_model = (member.get("model") or "").strip()

    if mid == "primary":
        duo = load_settings()
        provider = override_provider or duo.get("primary_provider") or settings.ai_provider or "ollama"
        model = override_model or duo.get("primary_model", "")
        if provider == "kimi" and not model:
            model = settings.kimi_model or "kimi-k2.6"
        return provider, model
    if mid == "local":
        provider = override_provider or "ollama"
        model = override_model or load_settings().get("local_model") or os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", "qwen2.5:3b")
        return provider, model

    provider = override_provider or member.get("provider", "openai")
    if override_model:
        return provider, override_model
    model_env = member.get("model_env")
    default_model = member.get("default_model", "")
    model = os.getenv(model_env, default_model) if model_env else default_model
    return provider, model


async def _member_say(member: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    """Have one council member speak.

    Each member gets a tight timeout so one slow/unreachable provider cannot stall
    the whole council. Cloud members do not fall back to the local Ollama queue;
    only the explicitly local member does, keeping the council fast.
    """
    mid = member["id"]
    system = _council_member_system(member, conv)
    messages = [{"role": "system", "content": system}] + _council_history(conv)
    provider, model = _resolve_member_model(member)
    content = ""
    used_provider = provider
    used_model = model
    error = ""
    member_timeout = float(os.getenv("SHIMS_DUOBOT_MEMBER_TIMEOUT_SECONDS", "12"))

    if ai_module:
        try:
            result = await asyncio.wait_for(
                ai_module.ask_ai(
                    prompt=messages[-1]["content"] if messages else "Please share your view.",
                    system=system,
                    provider=provider,
                    model=model,
                ),
                timeout=member_timeout,
            )
            content = result.text or ""
            used_provider = result.provider or provider
            used_model = result.model or model
        except Exception as exc:
            content = ""
            error = str(exc)[:200]
            used_provider = f"{provider}:error"

    # Only the local/Factory member falls back to Ollama. Falling every cloud member
    # back to the same local model creates a long Ollama queue and kills latency.
    if not content.strip() and provider in {"ollama", "local"}:
        fallback_model = os.getenv("SHIMS_DUOBOT_FALLBACK_MODEL", "qwen2.5:3b")
        try:
            fb = await asyncio.wait_for(
                asyncio.to_thread(_call_ollama_chat_sync, fallback_model, [
                    {"role": "system", "content": system},
                    {"role": "user", "content": (messages[-1]["content"] if messages else "Share your view.")},
                ]),
                timeout=member_timeout,
            )
            content = fb.get("content") or ""
            used_provider = "ollama"
            used_model = fallback_model
        except Exception as exc:
            error = str(exc)[:200]

    if not content.strip():
        content = f"[{member.get('name', mid)} could not respond: {error or 'no output'}]"

    return {
        "role": mid,
        "content": content,
        "ts": _now(),
        "metadata": {
            "provider": used_provider,
            "model": used_model,
            "persona": member.get("name", mid),
        },
    }


async def _chair_decide(conv: dict[str, Any], member_responses: list[dict[str, Any]]) -> dict[str, Any]:
    """The chair reads all council opinions and returns a final answer plus optional action plan."""
    settings_obj = conv.get("council_settings") or _default_council_settings()
    chair_id = settings_obj.get("chair", "primary")
    chair_member = next((p for p in conv.get("personas", []) if p.get("id") == chair_id), None)
    if not chair_member:
        chair_member = COUNCIL_PERSONAS.get("primary", COUNCIL_PERSONAS["gemini"])

    transcript = "\n\n".join(
        f"{r['metadata'].get('persona', r['role'])}: {r['content']}" for r in member_responses
    )
    user_request = ""
    for msg in reversed(conv.get("messages", [])):
        if msg.get("role") == "user":
            user_request = msg.get("content", "")
            break

    system = (
        "You are the Chair of the SHIMS Council of the Wises. "
        "You have read the opinions of every council member. "
        "Your job is to produce a final, authoritative answer to the user and, only if necessary, "
        "a concrete action plan. The user may ask about ANY topic, plan, or use case — not only SHIMS. "
        "When SHIMS tools can help (e.g., file search, shell, code patch, desktop bridge), include them. "
        "When the request is outside SHIMS, give plain actionable steps the user can follow. "
        "Do NOT make up actions that are not required by the user's request. "
        "If you include actions, each action must use a real SHIMS tool name and valid JSON arguments when a SHIMS tool applies."
    )
    prompt = (
        f"User request: {user_request}\n\n"
        f"Council opinions:\n{transcript}\n\n"
        "Return ONLY a JSON object with this schema:\n"
        '{"final_answer": "markdown prose for the user", '
        '"actions": [{"tool": "shell.run", "args": {"command": "..."}, "reason": "..."}]}\n'
        "If no action is needed, set actions to []."
    )

    provider, model = _resolve_member_model(chair_member)
    content = ""
    if ai_module:
        try:
            result = await asyncio.wait_for(
                ai_module.ask_ai(prompt, system=system, provider=provider, model=model),
                timeout=float(os.getenv("SHIMS_DUOBOT_CHAIR_TIMEOUT_SECONDS", "20")),
            )
            content = result.text or ""
        except Exception:
            content = ""

    if not content.strip():
        # Fallback: simple concatenation.
        return {
            "final_answer": "Council consensus:\n\n" + transcript,
            "actions": [],
        }

    parsed = _extract_json(content)
    if parsed and isinstance(parsed, dict):
        return {
            "final_answer": parsed.get("final_answer", content),
            "actions": parsed.get("actions", []) or [],
        }
    return {"final_answer": content, "actions": []}


async def _execute_council_actions(conv: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute council actions via the agent tool registry."""
    from shared import agent_tools
    settings_obj = conv.get("council_settings") or _default_council_settings()
    auto_execute = bool(settings_obj.get("auto_execute")) or bool(
        os.getenv("SHIMS_OMNIPOTENT_MODE", "").lower() in {"1", "true", "yes", "on"}
    )
    results: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    for i, action in enumerate(actions):
        tool = action.get("tool")
        args = action.get("args", {})
        reason = action.get("reason", "")
        if not tool:
            results.append({"tool": tool, "args": args, "ok": False, "error": "missing tool name"})
            continue
        try:
            result = agent_tools.run_tool(tool, args, allow_gated=auto_execute)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:500]}
        if result.get("needs_approval"):
            approval_id = new_id("council_approve")
            pending.append({
                "approval_id": approval_id,
                "tool": tool,
                "args": args,
                "reason": reason,
                "status": "pending",
                "remaining_actions": actions[i + 1 :],
            })
            results.append({"tool": tool, "args": args, "ok": False, "needs_approval": True, "approval_id": approval_id})
            break  # stop and wait for user approval before continuing
        results.append({"tool": tool, "args": args, "ok": bool(result.get("ok")), "result": result})

    return {"ok": not pending, "results": results, "pending": pending}


async def run_council_turn(conv_id: str) -> dict[str, Any]:
    """Run one full council round: every member speaks, then the chair decides and may act."""
    conv = get_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    if len(conv.get("messages", [])) >= MAX_MESSAGES:
        return {
            "ok": False,
            "error": f"This conversation has reached the {MAX_MESSAGES}-message safety limit.",
            "conversation": conv,
        }

    personas = conv.get("personas", [])
    if not personas:
        return {"ok": False, "error": "council mode requires personas; switch mode and back to council to reset."}

    # Check for pending actions first; if any, do not start a new round.
    pending = conv.get("pending_council_actions", [])
    if pending:
        return {
            "ok": False,
            "error": f"There are {len(pending)} pending council action(s) awaiting approval. Approve them before continuing.",
            "conversation": conv,
        }

    # Retrieve relevant SHIMS source context once and share it with all members.
    await _feed_council_context(conv_id)
    # Re-read so members actually see the context message just added (otherwise
    # the retrieved context is wasted until the next turn).
    conv = get_conversation(conv_id) or conv

    # Each council member speaks in parallel.
    member_tasks = [_member_say(p, conv) for p in personas]
    member_responses = await asyncio.gather(*member_tasks)
    for msg in member_responses:
        add_message(conv_id, msg["role"], msg["content"], msg.get("metadata"))

    # Chair decides.
    decision = await _chair_decide(conv, member_responses)
    chair_msg = {
        "role": "chair",
        "content": decision["final_answer"],
        "ts": _now(),
        "metadata": {"persona": "Chair", "actions": decision.get("actions", [])},
    }
    add_message(conv_id, chair_msg["role"], chair_msg["content"], chair_msg.get("metadata"))

    # Execute actions if any.
    action_summary = None
    if decision.get("actions"):
        action_summary = await _execute_council_actions(conv, decision["actions"])
        convs = _load_all_conversations()
        conv = convs.get(conv_id)
        if conv:
            conv.setdefault("council_action_log", []).append({
                "ts": _now(),
                "decision": decision["final_answer"],
                "summary": action_summary,
            })
            if action_summary.get("pending"):
                conv["pending_council_actions"] = conv.get("pending_council_actions", []) + action_summary["pending"]
            _rewrite_conversations(convs)

    return {
        "ok": True,
        "speaker": "council",
        "decision": decision,
        "action_summary": action_summary,
        "conversation": get_conversation(conv_id),
    }


async def run_council_turn_stream(conv_id: str):
    """Streaming variant of :func:`run_council_turn`.

    Yields event dicts as each council member finishes speaking (members run in
    parallel, so they surface in genuine completion order — a live debate), then
    the chair's verdict, any action summary, and a final ``done`` event carrying
    the refreshed conversation.
    """
    conv = get_conversation(conv_id)
    if not conv:
        yield {"type": "error", "error": "conversation not found"}
        return
    if len(conv.get("messages", [])) >= MAX_MESSAGES:
        yield {"type": "error", "error": f"This conversation has reached the {MAX_MESSAGES}-message safety limit."}
        return

    personas = conv.get("personas", [])
    if not personas:
        yield {"type": "error", "error": "council mode requires personas; switch mode and back to council to reset."}
        return

    pending = conv.get("pending_council_actions", [])
    if pending:
        yield {"type": "error", "error": f"There are {len(pending)} pending council action(s) awaiting approval. Approve them before continuing."}
        return

    await _feed_council_context(conv_id)
    # Re-read so members see the freshly added RAG context (parity with the
    # non-streaming path; otherwise the retrieved context is wasted this turn).
    conv = get_conversation(conv_id) or conv
    yield {"type": "council_start",
           "members": [{"role": p["id"], "name": p.get("name", p["id"])} for p in personas]}

    # Run all members in parallel; surface each as it completes (real debate order).
    tasks = [asyncio.ensure_future(_member_say(p, conv)) for p in personas]
    member_responses: list[dict[str, Any]] = []
    for fut in asyncio.as_completed(tasks):
        try:
            msg = await fut
        except Exception as exc:  # pragma: no cover - defensive
            yield {"type": "member_error", "error": str(exc)[:200]}
            continue
        add_message(conv_id, msg["role"], msg["content"], msg.get("metadata"))
        member_responses.append(msg)
        yield {"type": "message", "message": msg}

    # Chair synthesises the verdict.
    yield {"type": "chair_start"}
    decision = await _chair_decide(conv, member_responses)
    chair_msg = {
        "role": "chair",
        "content": decision["final_answer"],
        "ts": _now(),
        "metadata": {"persona": "Chair", "actions": decision.get("actions", [])},
    }
    add_message(conv_id, chair_msg["role"], chair_msg["content"], chair_msg.get("metadata"))
    yield {"type": "message", "message": chair_msg}

    # Execute actions if any (mirrors run_council_turn's persistence).
    action_summary = None
    if decision.get("actions"):
        action_summary = await _execute_council_actions(conv, decision["actions"])
        convs = _load_all_conversations()
        conv2 = convs.get(conv_id)
        if conv2:
            conv2.setdefault("council_action_log", []).append({
                "ts": _now(),
                "decision": decision["final_answer"],
                "summary": action_summary,
            })
            if action_summary.get("pending"):
                conv2["pending_council_actions"] = conv2.get("pending_council_actions", []) + action_summary["pending"]
            _rewrite_conversations(convs)
        yield {"type": "action_summary", "summary": action_summary}

    yield {"type": "done", "conversation": get_conversation(conv_id)}


def approve_council_action(conv_id: str, approval_id: str) -> dict[str, Any]:
    """Approve a pending council action and continue executing the remaining action list."""
    from shared import agent_tools
    convs = _load_all_conversations()
    conv = convs.get(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    pending = conv.get("pending_council_actions", [])
    idx = next((i for i, a in enumerate(pending) if a.get("approval_id") == approval_id), -1)
    if idx == -1:
        return {"ok": False, "error": "approval not found"}

    action = pending[idx]
    result = agent_tools.run_tool(action["tool"], action.get("args", {}), allow_gated=True)
    action["status"] = "approved" if result.get("ok") else "failed"
    action["result"] = result

    # Remove from pending and execute the remaining action list.
    remaining_actions = action.get("remaining_actions", [])
    pending.pop(idx)

    remaining_results: list[dict[str, Any]] = []
    auto_execute = bool(conv.get("council_settings", {}).get("auto_execute"))
    for ridx, next_action in enumerate(remaining_actions):
        tool = next_action.get("tool")
        args = next_action.get("args", {})
        reason = next_action.get("reason", "")
        if not tool:
            remaining_results.append({"tool": tool, "args": args, "ok": False, "error": "missing tool name"})
            continue
        try:
            next_result = agent_tools.run_tool(tool, args, allow_gated=auto_execute)
        except Exception as exc:
            next_result = {"ok": False, "error": str(exc)[:500]}
        if next_result.get("needs_approval"):
            new_id_val = new_id("council_approve")
            pending.append({
                "approval_id": new_id_val,
                "tool": tool,
                "args": args,
                "reason": reason,
                "status": "pending",
                "remaining_actions": remaining_actions[ridx + 1 :],
            })
            remaining_results.append({"tool": tool, "args": args, "ok": False, "needs_approval": True, "approval_id": new_id_val})
            break
        remaining_results.append({"tool": tool, "args": args, "ok": bool(next_result.get("ok")), "result": next_result})

    conv["pending_council_actions"] = pending
    conv.setdefault("council_action_log", []).append({
        "ts": _now(),
        "approval_id": approval_id,
        "approved_action": action,
        "remaining_results": remaining_results,
    })
    _rewrite_conversations(convs)
    return {"ok": True, "approved": action, "remaining": remaining_results, "conversation": get_conversation(conv_id)}


def reject_council_action(conv_id: str, approval_id: str) -> dict[str, Any]:
    """Reject a pending council action and drop the rest of the pending queue."""
    convs = _load_all_conversations()
    conv = convs.get(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    pending = conv.get("pending_council_actions", [])
    idx = next((i for i, a in enumerate(pending) if a.get("approval_id") == approval_id), -1)
    if idx == -1:
        return {"ok": False, "error": "approval not found"}
    rejected = pending[idx:]
    conv["pending_council_actions"] = []
    conv.setdefault("council_action_log", []).append({
        "ts": _now(),
        "approval_id": approval_id,
        "rejected": rejected,
    })
    _rewrite_conversations(convs)
    return {"ok": True, "rejected": rejected, "conversation": get_conversation(conv_id)}


async def run_turn(conv_id: str) -> dict[str, Any]:
    conv = get_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    if len(conv.get("messages", [])) >= MAX_MESSAGES:
        return {
            "ok": False,
            "error": f"This conversation has reached the {MAX_MESSAGES}-message safety limit. Please finalize or start a new conversation.",
            "conversation": conv,
        }
    if conv.get("mode") == "council":
        return await run_council_turn(conv_id)
    domain = _domain_profile(conv)
    if conv.get("mode") == "improvement" or domain.get("manufacturing") or domain.get("chemistry") or domain.get("latency"):
        await refresh_conversation_capabilities(conv_id, force=False)
        conv = get_conversation(conv_id) or conv
    speaker = "primary" if _last_speaker(conv) == "local" else "local"
    if speaker == "primary":
        msg = await _primary_say(conv)
    else:
        msg = await _local_say(conv)
    # Prevent loops caused by duplicate or stuck responses.
    if _is_duplicate(conv, msg.get("content", ""), msg.get("role", speaker)):
        return {
            "ok": False,
            "error": "The next speaker would repeat a recent message (conversation is stuck). Add a user message, switch mode, finalize, or reset.",
            "conversation": conv,
        }
    add_message(conv_id, msg["role"], msg["content"], msg.get("metadata"))
    return {"ok": True, "speaker": speaker, "conversation": get_conversation(conv_id)}


async def finalize_conversation(conv_id: str) -> dict[str, Any]:
    """Ask primary Omni to produce a concise final answer/summary of the chat."""
    conv = get_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    transcript_lines: list[str] = []
    for msg in conv.get("messages", []):
        role = msg.get("role")
        if role == "user":
            transcript_lines.append(f"User: {msg['content']}")
        elif role == "primary":
            transcript_lines.append(f"Omni: {msg['content']}")
        elif role == "local":
            transcript_lines.append(f"Factory: {msg['content']}")
    transcript = "\n\n".join(transcript_lines)
    prompt = (
        "You are SHIMS Omni. Review the following conversation between you, the Local Factory, and the user. "
        "Produce a concise, authoritative final answer or summary that resolves the topic. "
        "If there were conflicting views, state the agreed conclusion.\n\n"
        f"{transcript}\n\nFinal answer:"
    )
    system = _system_prompt("primary", conv.get("mode", "free"), domain=_domain_profile(conv), capabilities=conv.get("capabilities"))
    content = ""
    duo_settings = load_settings()
    provider = duo_settings.get("primary_provider") or settings.ai_provider or "ollama"
    model = duo_settings.get("primary_model", "")
    if provider == "kimi" and not model:
        model = settings.kimi_model or "kimi-k2.6"

    if ai_module:
        try:
            result = await asyncio.wait_for(
                ai_module.ask_ai(prompt, system=system, provider=provider, model=model),
                timeout=90.0,
            )
            content = result.text or ""
        except Exception:
            content = ""

    if not content.strip():
        fallback_model = os.getenv("SHIMS_DUOBOT_FALLBACK_MODEL", "qwen2.5:3b")
        fb = await asyncio.to_thread(_call_ollama_chat_sync, fallback_model, [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])
        content = fb.get("content") or ""

    if not content.strip():
        content = "Could not generate a final answer."

    add_message(conv_id, "final", content, {"provider": provider, "model": model})
    return {"ok": True, "final": content, "conversation": get_conversation(conv_id)}


def get_pending_proposals(limit: int = 50) -> list[dict[str, Any]]:
    """Aggregate improvement proposals from local improvement loop and peer sync."""
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Local improvement-loop proposals (from recent runs)
    improvement_dir = STORAGE_DIR / "improvement_loop"
    for p in sorted(improvement_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            run = json.loads(p.read_text(encoding="utf-8"))
            for prop in run.get("proposals", []):
                pid = prop.get("patch_id") or prop.get("skill_id") or prop.get("variant_id") or str(uuid.uuid4())
                if pid in seen:
                    continue
                seen.add(pid)
                proposals.append({
                    "id": pid,
                    "source": "primary",
                    "source_run": run.get("run_id"),
                    "type": prop.get("type", "unknown"),
                    "proposal": prop,
                    "created_at": run.get("finished_at"),
                })
        except Exception:
            continue

    # Peer-synced proposals (from local factory)
    peer_path = STORAGE_DIR / "peer_sync" / "proposals.jsonl"
    if peer_path.exists():
        for entry in _jsonl_read(peer_path, limit=limit):
            prop = entry.get("proposal") or {}
            pid = prop.get("patch_id") or prop.get("skill_id") or prop.get("variant_id") or str(uuid.uuid4())
            if pid in seen:
                continue
            seen.add(pid)
            proposals.append({
                "id": pid,
                "source": "local",
                "type": prop.get("type", "unknown"),
                "proposal": prop,
                "received_at": entry.get("received_at"),
            })

    return sorted(proposals, key=lambda x: x.get("created_at") or x.get("received_at") or 0, reverse=True)[:limit]


def record_vote(proposal_id: str, action: str, user: str = "user") -> dict[str, Any]:
    action = action.lower().strip()
    if action not in ("approve", "reject"):
        return {"ok": False, "error": "action must be approve or reject"}
    entry = {"proposal_id": proposal_id, "action": action, "user": user, "ts": _now()}
    _jsonl_append(VOTES_PATH, [entry])
    return {"ok": True, "vote": entry}


def get_votes() -> dict[str, str]:
    votes: dict[str, str] = {}
    for entry in _jsonl_read(VOTES_PATH):
        pid = entry.get("proposal_id")
        if pid:
            votes[pid] = entry.get("action", "")
    return votes


def apply_approved_proposal(proposal_id: str) -> dict[str, Any]:
    """Best-effort apply a proposal that has been approved by the user."""
    proposals = get_pending_proposals(limit=200)
    prop = next((p for p in proposals if p.get("id") == proposal_id), None)
    if not prop:
        return {"ok": False, "error": "proposal not found"}

    ptype = prop.get("type")
    detail = prop.get("proposal", {})

    if ptype == "patch":
        patch_id = detail.get("patch_id") or proposal_id
        return apply_proposal(patch_id)
    if ptype == "prompt_variant":
        variant_id = detail.get("variant_id") or proposal_id
        return promote_variant(variant_id)
    if ptype == "skill":
        return {"ok": True, "message": "Skill is already saved; approve action recorded."}

    return {"ok": False, "error": f"apply not implemented for proposal type {ptype}"}


def delete_proposal(proposal_id: str) -> dict[str, Any]:
    """Permanently remove a proposal from the active improvement-loop runs and peer sync.

    For patch proposals the underlying self-evolver proposal is marked rejected so it
    will not be re-surfaced.
    """
    deleted_from: list[str] = []

    # Remove from improvement-loop run files.
    improvement_dir = STORAGE_DIR / "improvement_loop"
    for p in improvement_dir.glob("*.json"):
        try:
            run = json.loads(p.read_text(encoding="utf-8"))
            before = len(run.get("proposals", []))
            run["proposals"] = [
                prop for prop in run.get("proposals", [])
                if _proposal_id(prop) != proposal_id
            ]
            if len(run["proposals"]) < before:
                _save_run(run.get("run_id"), run)
                deleted_from.append(str(p.name))
        except Exception:
            continue

    # Remove from peer sync.
    peer_path = STORAGE_DIR / "peer_sync" / "proposals.jsonl"
    if peer_path.exists():
        entries = _jsonl_read(peer_path)
        before = len(entries)
        entries = [
            e for e in entries
            if _proposal_id(e.get("proposal") or {}) != proposal_id
        ]
        if len(entries) < before:
            peer_path.write_text(
                "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in entries) + "\n",
                encoding="utf-8",
            )
            deleted_from.append("peer_sync")

    # Mark underlying self-evolver patch rejected if present.
    try:
        from .self_evolver import _load_proposal as _se_load, _save_proposal as _se_save
        se_prop = _se_load(proposal_id)
        if se_prop and se_prop.get("id"):
            se_prop["status"] = "rejected"
            se_prop["rejected_by"] = "user"
            se_prop["rejected_at"] = _now()
            _se_save(se_prop)
            deleted_from.append("self_evolver")
    except Exception:
        pass

    if not deleted_from:
        return {"ok": False, "error": "proposal not found"}
    return {"ok": True, "deleted_from": deleted_from}


def _proposal_id(prop: dict[str, Any]) -> str:
    return prop.get("patch_id") or prop.get("skill_id") or prop.get("variant_id") or ""


def rethink_proposal(proposal_id: str, feedback: str = "") -> dict[str, Any]:
    """Reject a proposal, record why, and queue a request for an alternative.

    The original proposal is deleted from the active list and a 'rethink' entry is
    written to the DuoBot vote ledger. The next improvement-loop run can read
    rethink feedback from the ledger to avoid proposing the same rejected idea.
    """
    # Record the reject + rethink vote.
    record_vote(proposal_id, "reject")
    entry = {
        "proposal_id": proposal_id,
        "action": "rethink",
        "feedback": feedback,
        "user": "user",
        "ts": _now(),
    }
    _jsonl_append(VOTES_PATH, [entry])

    # Delete from active lists.
    del_result = delete_proposal(proposal_id)
    if not del_result["ok"]:
        return del_result

    return {"ok": True, "message": "Proposal rejected and queued for rethink.", "feedback": feedback}


def get_rethink_feedback(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent rethink entries for improvement-loop prompts."""
    feedback: list[dict[str, Any]] = []
    for entry in _jsonl_read(VOTES_PATH):
        if entry.get("action") == "rethink":
            feedback.append(entry)
    return feedback[-limit:]
