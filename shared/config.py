from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    # Allow an alternate env file (e.g., .env.local) for isolated instances.
    env_file = Path(os.getenv('SHIMS_ENV_FILE', ROOT_DIR / '.env')).expanduser().resolve()
    if not env_file.exists():
        env_file = ROOT_DIR / '.env'
    # Load .env values so they take precedence over system env vars.
    # This ensures SHIMS-specific config (KIMI_BASE_URL, etc.) is honored.
    load_dotenv(env_file, override=True)
except Exception:
    pass

STORAGE_DIR = Path(os.getenv('SHIMS_STORAGE_DIR', ROOT_DIR / 'storage')).resolve()
GENERATED_DIR = STORAGE_DIR / 'generated'
SANDBOX_DIR = STORAGE_DIR / 'sandbox'
BACKUP_DIR = STORAGE_DIR / 'backups'

for directory in (STORAGE_DIR, GENERATED_DIR, SANDBOX_DIR, BACKUP_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


@dataclass
class Settings:
    app_name: str = os.getenv('SHIMS_APP_NAME', 'SHIMS')
    environment: str = os.getenv('SHIMS_ENV', 'local')
    secret_key: str = os.getenv('SHIMS_SECRET_KEY', 'change-me-local-secret')
    database_path: Path = Path(os.getenv('SHIMS_DB_PATH', STORAGE_DIR / 'shims.sqlite3')).resolve()

    host: str = os.getenv('SHIMS_HOST', '127.0.0.1')
    omni_port: int = env_int('SHIMS_OMNI_PORT', 8010)
    enterprise_port: int = env_int('SHIMS_ENTERPRISE_PORT', 8020)

    ollama_base_url: str = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    ollama_model: str = os.getenv('OLLAMA_MODEL', 'llama3.2:latest')
    ai_provider: str = os.getenv('SHIMS_AI_PROVIDER', 'ollama')
    openai_api_key: str = os.getenv('OPENAI_API_KEY', '')
    openai_model: str = os.getenv('OPENAI_MODEL', 'gpt-4o')
    anthropic_api_key: str = os.getenv('ANTHROPIC_API_KEY', '')
    anthropic_model: str = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')
    google_api_key: str = os.getenv('GOOGLE_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    gemini_model: str = os.getenv('GEMINI_MODEL', 'gemini-2.5-pro')
    qwen_api_key: str = os.getenv('QWEN_API_KEY', '')
    qwen_model: str = os.getenv('QWEN_MODEL', 'qwen-max')
    qwen_base_url: str = os.getenv('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')

    kimi_api_key: str = os.getenv('KIMI_API_KEY', '')
    kimi_model: str = os.getenv('KIMI_MODEL', 'moonshot-v1-8k')
    kimi_base_url: str = os.getenv('KIMI_BASE_URL', 'https://api.moonshot.ai/v1')

    deepseek_api_key: str = os.getenv('DEEPSEEK_API_KEY', '')
    deepseek_model: str = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    deepseek_base_url: str = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')

    huggingface_base_url: str = os.getenv('HUGGINGFACE_BASE_URL', 'http://127.0.0.1:8080')
    huggingface_api_key: str = os.getenv('HUGGINGFACE_API_KEY', '')
    huggingface_model: str = os.getenv('HUGGINGFACE_MODEL', 'meta-llama/Llama-3.1-8B-Instruct')

    enterprise_url: str = os.getenv('SHIMS_ENTERPRISE_URL', 'http://127.0.0.1:8020')
    bridge_token: str = os.getenv('SHIMS_BRIDGE_TOKEN', 'change-me-bridge-token')
    enterprise_pairing_enabled: bool = env_bool('SHIMS_ENTERPRISE_PAIRING_ENABLED', False)

    demo_mode: bool = env_bool('SHIMS_DEMO_MODE', True)
    allow_self_evolution: bool = env_bool('SHIMS_ALLOW_SELF_EVOLUTION', False)
    omnipotent_mode: bool = env_bool('SHIMS_OMNIPOTENT_MODE', False)
    auto_evolution: bool = env_bool('SHIMS_AUTO_EVOLUTION', False)
    self_evolution_model: str = os.getenv('SHIMS_SELF_EVOLUTION_MODEL', 'qwen2.5-coder:14b')
    code_timeout_seconds: int = env_int('SHIMS_CODE_TIMEOUT_SECONDS', 15)
    max_output_tokens: int = env_int('SHIMS_MAX_OUTPUT_TOKENS', 64000)

    # Voice provider freeze: 'cloud' (OpenAI Whisper/TTS), 'fast' (browser + local fallback), 'local' (local only), 'offline' (no cloud).
    voice_mode: str = os.getenv('SHIMS_VOICE_MODE', 'fast').lower()

    # Manufacturing mode: 'api_only' (no formulation unit) or 'formulation' (tablets/capsules/etc)
    manufacturing_mode: str = os.getenv('SHIMS_MANUFACTURING_MODE', 'api_only')
    # Site phase: 'setup' (draft-friendly) or 'gmp' (21 CFR locked down)
    site_phase: str = os.getenv('SHIMS_SITE_PHASE', 'setup')

    # Rate limiting
    rate_limit_requests: int = env_int('SHIMS_RATE_LIMIT_REQUESTS', 60)
    rate_limit_window: int = env_int('SHIMS_RATE_LIMIT_WINDOW_SECONDS', 60)

    def __post_init__(self) -> None:
        # Normalize Kimi model names so "k2.6" → "kimi-k2.6" automatically.
        try:
            from .kimi_model_helper import normalize_kimi_model
            self.kimi_model = normalize_kimi_model(self.kimi_model)
        except Exception:
            pass

    def __setattr__(self, name: str, value: Any) -> None:
        # Allow __post_init__ to mutate fields despite frozen dataclass.
        if name == 'kimi_model':
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


def _validate_settings() -> None:
    from .guardians import is_weak_secret
    for env_var in ('SHIMS_SECRET_KEY', 'SHIMS_BRIDGE_TOKEN', 'ENTERPRISE_BRIDGE_TOKEN'):
        if is_weak_secret(env_var, os.getenv(env_var, '')):
            warnings.warn(
                f"{env_var} is not set or uses a weak/default value. "
                f"Set a strong unique value in .env before production use.",
                stacklevel=2,
            )
    # Safety: block omnipotent/demo modes in production/plant environments.
    env = os.getenv('SHIMS_ENV', 'local').lower()
    if env in {'production', 'prod', 'plant'}:
        if os.getenv('SHIMS_OMNIPOTENT_MODE', '').lower() in {'1', 'true', 'yes', 'on'}:
            warnings.warn("SHIMS_OMNIPOTENT_MODE is enabled in production/plant environment.", RuntimeWarning, stacklevel=2)
        if os.getenv('SHIMS_DEMO_MODE', '').lower() in {'1', 'true', 'yes', 'on'}:
            warnings.warn("SHIMS_DEMO_MODE is enabled in production/plant environment.", RuntimeWarning, stacklevel=2)


settings = Settings()
_validate_settings()
