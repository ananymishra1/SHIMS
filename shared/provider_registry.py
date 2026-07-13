from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional
LOCAL_HINTS=("llama","qwen","mistral","codellama","phi","gemma","deepseek-r1","nomic","mixtral")
CLOUD_HINTS={"anthropic":("claude","sonnet","haiku","opus"),"openai":("gpt","o1","o3","o4","openai"),"gemini":("gemini",),"kimi":("kimi","moonshot"),"deepseek":("deepseek-chat",)}
PROVIDER_ENV={"openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","gemini":"GEMINI_API_KEY","kimi":"KIMI_API_KEY","deepseek":"DEEPSEEK_API_KEY"}

def _lmstudio_available() -> bool:
    """Check if LM Studio server is responding on its default port."""
    import urllib.request
    try:
        url = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234").rstrip("/") + "/v1/models"
        req = urllib.request.Request(url, method="GET", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

@dataclass(frozen=True)
class ProviderDecision:
    provider: str; model: str; reason: str
def clean_secret(value: str | None) -> str:
    if not value: return ''
    v=str(value).strip().strip('"').strip("'").strip()
    if v.lower().startswith('bearer '): v=v[7:].strip()
    return v
def cloud_provider_from_model(model: str | None) -> Optional[str]:
    m=(model or '').strip().lower()
    for p,hints in CLOUD_HINTS.items():
        if any(h in m for h in hints): return p
    return None
def looks_local_model(model: str | None) -> bool:
    m=(model or '').strip().lower()
    return bool(m and (any(m.startswith(x) or (x+':') in m for x in LOCAL_HINTS) or (':' in m and not cloud_provider_from_model(m))))
def provider_configured(provider: str) -> bool:
    if provider=='ollama': return True
    if provider=='lmstudio': return _lmstudio_available()
    env=PROVIDER_ENV.get(provider); return bool(env and clean_secret(os.getenv(env)))
def decide_provider(provider: str | None, model: str | None, *, installed_local: list[str] | None=None, default_local: str='llama3.2:latest', provider_defaults: dict[str,str] | None=None) -> ProviderDecision:
    installed=set(installed_local or []); defaults=provider_defaults or {'ollama':default_local,'anthropic':'claude-sonnet-4-6','openai':'gpt-4o-mini','gemini':'gemini-1.5-pro','lmstudio':'google/gemma-4-e4b'}
    rp=(provider or 'auto').strip().lower() or 'auto'; rm=(model or '').strip()
    if rp in {'lmstudio'}:
        if _lmstudio_available(): return ProviderDecision('lmstudio', rm or defaults.get('lmstudio', 'google/gemma-4-e4b'), 'selected-lmstudio')
        return ProviderDecision('ollama', rm or default_local, 'lmstudio-unavailable-fallback-local')
    if rp in {'local','ollama'}:
        if rm and (rm in installed or looks_local_model(rm)): return ProviderDecision('ollama', rm, 'selected-local')
        return ProviderDecision('ollama', default_local, 'forced-local-from-provider')
    if rm and (looks_local_model(rm) or rm in installed): return ProviderDecision('ollama', rm, 'local-model-overrides-stale-provider')
    cloud=cloud_provider_from_model(rm)
    if cloud:
        if provider_configured(cloud): return ProviderDecision(cloud, rm or defaults.get(cloud, rm), 'cloud-model-selected')
        if installed: return ProviderDecision('ollama', default_local, f'cloud-{cloud}-not-configured-fallback-local')
        return ProviderDecision(cloud, rm or defaults.get(cloud, rm), 'cloud-model-no-local-fallback')
    if rp not in {'auto','ollama'} and rp in defaults:
        if not provider_configured(rp) and installed: return ProviderDecision('ollama', default_local, f'cloud-{rp}-not-configured-fallback-local')
        return ProviderDecision(rp, rm or defaults[rp], 'explicit-cloud-provider')
    # Auto: prefer LM Studio if available, then Ollama
    if _lmstudio_available():
        return ProviderDecision('lmstudio', rm or defaults.get('lmstudio', 'google/gemma-4-e4b'), 'auto-lmstudio-available')
    return ProviderDecision('ollama', rm or default_local, 'auto-local-first')


# ---------------------------------------------------------------------------
# v15-compatible resolve_provider — merged from provider_registry_patch.py
# ---------------------------------------------------------------------------
OLLAMA_MODEL_HINTS = ["llama", "qwen", "mistral", "deepseek", "gemma", "phi", "codellama", "nomic"]


def infer_provider_from_model(model: str) -> str | None:
    m = (model or "").lower().strip()
    if not m:
        return None
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    if m.startswith("gemini"):
        return "gemini"
    if any(x in m for x in OLLAMA_MODEL_HINTS) or ":" in m:
        return "ollama"
    return None


def resolve_provider(provider: str | None, model: str | None, force_local: bool = False) -> tuple[str, str]:
    provider = (provider or "auto").lower().strip()
    model = (model or "").strip()
    if force_local:
        return "ollama", model if (model and infer_provider_from_model(model) == "ollama") else "llama3.2:latest"
    if provider == "lmstudio":
        return "lmstudio", model or "google/gemma-4-e4b"
    inferred = infer_provider_from_model(model)
    if inferred in {"anthropic", "openai", "gemini"}:
        return inferred, model
    if provider in {"anthropic", "openai", "gemini", "ollama", "lmstudio"}:
        if provider == "ollama" and inferred in {"anthropic", "openai", "gemini"}:
            return inferred, model
        return provider, model or "llama3.2:latest"
    return inferred or "ollama", model or "llama3.2:latest"
