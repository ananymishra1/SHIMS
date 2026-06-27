# SHIMS One-Click Deployment

This folder contains everything you need to deploy SHIMS with minimal friction while keeping **self-evolution** fully functional.

---

## Quick Pick

| Method | Best For | Self-Evolution | One-Click? |
|---|---|---|---|
| `one-click.bat` | Windows users who want a native install | ✅ Yes — runs from filesystem | ✅ Double-click |
| `docker-compose.self-evolving.yml` | Docker users, servers, reproducible environments | ✅ Yes — source is volume-mounted | `docker compose up -d` |

---

## `one-click.bat` (Windows)

### What it does
1. Downloads and installs `uv` (Astral's fast Python package manager) if missing
2. Installs Python 3.12 via `uv`
3. Creates a `.venv` and installs all dependencies
4. Installs Playwright Chromium browser
5. Creates a starter `.env` with safe defaults
6. Launches SHIMS (Desktop Bridge + Omni)

### How to use
1. Clone or unzip SHIMS to a short path like `C:\SHIMS`
2. Double-click `deploy\one-click.bat`
3. Wait for dependencies to install (first run: ~5-10 minutes)
4. Open http://localhost:8010

### Self-evolution
Because SHIMS runs natively from the filesystem, it can rewrite its own source files in `shared/`, `backend/`, and `apps/` exactly as designed. Nothing is locked inside a container image.

---

## `docker-compose.self-evolving.yml` (Docker)

### What it does
Builds the SHIMS image but mounts the source code, storage, and generated directories as **writable volumes**. This lets the container write back to the host filesystem, preserving self-evolution.

### How to use
```bash
# From the repo root
docker compose -f deploy/docker-compose.self-evolving.yml up -d

# Tail logs
docker compose -f deploy/docker-compose.self-evolving.yml logs -f shims-omni

# Stop
docker compose -f deploy/docker-compose.self-evolving.yml down
```

### Self-evolution
Three host directories are mounted into the container:
- `./shared` → `/app/shared` (agent tools, memory, planner, brain)
- `./backend` → `/app/backend` (FastAPI core)
- `./apps` → `/app/apps` (App Factory output)
- `./storage` → `/app/storage` (DB, backups, state)
- `./data` → `/app/data` (vector memory, brain state)
- `./generated` → `/app/generated` (documents, artifacts)

When SHIMS self-evolution writes a file, it lands on the host disk immediately. You can inspect changes with `git diff`.

---

## Post-Install Checklist

1. **Edit `.env`** and add your API keys for any cloud providers you want:
   - `KIMI_API_KEY` (Kimi / Moonshot)
   - `OPENAI_API_KEY`
   - `ANTHROPIC_API_KEY`
   - `GEMINI_API_KEY`
   - `DEEPSEEK_API_KEY`
   - `QWEN_API_KEY`

2. **Set `KIMI_MODEL`** to a model your account can access:
   - `kimi-k2.6` (newest, if available on your account)
   - `kimi-k2.5`
   - `moonshot-v1-128k` (safest default)
   - You can also use shorthand: `k2.6`, `k2.5`, `128k`

3. **Install Ollama** (optional but recommended for local-only mode):
   ```bash
   ollama pull qwen2.5:7b
   # or for low RAM:
   ollama pull llama3.2:3b
   ```

4. **Enable self-evolution** (off by default for safety):
   ```bash
   # In .env
   SHIMS_ALLOW_SELF_EVOLUTION=true
   SHIMS_AUTO_EVOLUTION=false   # set true only if you want unattended patches
   ```

---

## Troubleshooting

### "Microsoft Visual C++ 14.0 is required"
The `one-click.bat` handles this by installing `webrtcvad-wheels` instead of the source package. If you still see build errors, install the [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with "Desktop development with C++" workload.

### "DLL load failed while importing _ssl"
This means the bundled Python can't use Windows SSL due to App Control policies. The `one-click.bat` uses `uv` to install a fresh Python that works correctly.

### "Model not found (404)" on Kimi
SHIMS now auto-normalizes model names and retries with fallback models:
- `k2.6` → `kimi-k2.6` → `kimi-k2.5` → `moonshot-v1-128k` → ...
- If your account doesn't have `kimi-k2.6`, it will transparently fall back to a working model.

---

## License

Same as SHIMS: Elastic License 2.0.
