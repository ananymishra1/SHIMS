from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parents[1]


def load_env_file(path: Path | None = None) -> None:
    path = path or BASE_DIR / '.env'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


load_env_file()


@dataclass
class Settings:
    secret_key: str = env('SHIMS_SECRET_KEY', 'dev-secret-change-me')
    bridge_token: str = env('ENTERPRISE_BRIDGE_TOKEN', 'change-this-bridge-token')
    company_name: str = env('COMPANY_NAME', 'J K Lifecare Centers Private Limited')
    company_gst: str = env('COMPANY_GST', '23AAECJ6427F1ZS')
    company_address: str = env('COMPANY_ADDRESS', 'Plot No. 97, DMIC VUL, Ujjain, M.P., India 456664')
    company_phone: str = env('COMPANY_PHONE', '+917000452122')
    company_email: str = env('COMPANY_EMAIL', 'info@jklifecarecenters.com')
    data_dir: Path = Path(env('DATA_DIR', './data'))
    generated_dir: Path = Path(env('GENERATED_DIR', './generated'))
    workspace_dir: Path = Path(env('WORKSPACE_DIR', './workspace'))
    omni_database_url: str = env('OMNI_DATABASE_URL', 'sqlite:///./data/shims_omni.db')
    enterprise_database_url: str = env('ENTERPRISE_DATABASE_URL', 'sqlite:///./data/shims_enterprise.db')
    llm_provider: str = env('LLM_PROVIDER', 'ollama')
    ollama_base_url: str = env('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    ollama_model: str = env('OLLAMA_MODEL', 'llama3.1:8b')
    openai_api_key: str = env('OPENAI_API_KEY', '')
    openai_model: str = env('OPENAI_MODEL', 'gpt-4.1-mini')
    gemini_api_key: str = env('GEMINI_API_KEY', '')
    gemini_model: str = env('GEMINI_MODEL', 'gemini-2.5-pro')
    self_evolution_enabled: bool = env('SELF_EVOLUTION_ENABLED', 'true').lower() == 'true'
    self_evolution_require_tests: bool = env('SELF_EVOLUTION_REQUIRE_TESTS', 'false').lower() == 'true'
    self_evolution_allowed_paths: str = env('SELF_EVOLUTION_ALLOWED_PATHS', 'apps,shims_core,tests,docs')

    def prepare(self) -> 'Settings':
        for p in [self.data_dir, self.generated_dir, self.workspace_dir]:
            path = p if p.is_absolute() else BASE_DIR / p
            path.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def allowed_paths(self) -> List[str]:
        return [p.strip().replace('\\', '/') for p in self.self_evolution_allowed_paths.split(',') if p.strip()]


settings = Settings().prepare()


def resolve_sqlite_url(url: str) -> str:
    if url.startswith('sqlite:///./'):
        rel = url.replace('sqlite:///./', '')
        path = BASE_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return 'sqlite:///' + path.as_posix()
    return url
