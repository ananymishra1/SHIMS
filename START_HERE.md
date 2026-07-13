# SHIMS — Start Here

Two things in this repo:

| Product | What it is | URL |
|---|---|---|
| **Omni** | Personal AI agent (chat, mail, calendar, media, chemistry, self-learning) | http://127.0.0.1:8010 |
| **Council of the Wise** | Multi-agent debate mode (Omni, Gemini, Claude, GPT) | http://127.0.0.1:8010/omni-duobot |

## Quick start (Windows)

```bat
setup.bat        :: one time — creates .venv and installs everything
START_SHIMS.bat  :: launches Desktop Bridge + Omni
```

## Pre-flight checks

```bat
.venv\Scripts\python scripts\smoke_llm.py   :: verify local LLM (Ollama) is live
.venv\Scripts\python -m pytest -q           :: run the full test suite
```

## Configuration

Copy `.env.example` → `.env` (setup.bat does this) and fill in what you need:
local model (`SHIMS_OLLAMA_MODEL`), optional cloud keys, Gmail OAuth
(`SHIMS_GMAIL_CLIENT_ID/SECRET` + a send scope to enable reply), web-search keys.

## Layout

- `backend/app/main.py` — Omni FastAPI app
- `shared/` — shared engine (documents, mailbox, chem brain, omni brain, …)
- `apps/` — vertical apps (Todo Demo, Sheena AI Wellness)
- `tests/` — test suite · `scripts/` — utilities
