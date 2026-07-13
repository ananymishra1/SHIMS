# AGENTS.md — SHIMS Agent OS

Agent-focused guidance for coding on the SHIMS (Self-Hosted Intelligent Multi-agent System) codebase.

---

## Wave Engine v3 Architecture

SHIMS Agent OS v3 replaces the sequential step loop with a **wave-based execution engine**. The goal is Hermes-class latency while keeping SHIMS's deep desktop integration.

### Core concepts

- **Wave**: a set of independent tool calls emitted by the LLM in a single turn and executed in parallel.
- **Router**: a fast, cheap model that plans each wave (which tools to call in parallel).
- **Executor**: the main model that synthesizes the final answer from tool results.
- **Wave plan**: a JSON object the router emits:
  ```json
  {
    "wave": [
      {"tool": "fs.list", "args": {"path": "."}, "purpose": "List files"},
      {"tool": "web.search", "args": {"query": "..."}, "purpose": "Find docs"}
    ],
    "reasoning": "why these tools in parallel",
    "final": null
  }
  ```
  When `"final"` is a string, the loop stops and returns it as the answer.

### Files

| File | Purpose |
|------|---------|
| `shared/agent_wave.py` | Wave planner (`plan_wave`), parallel executor (`execute_wave`), duplicate suppression, context builder. |
| `shared/agent_loop.py` | `run_agent_loop()` drives the wave loop, telemetry, scratchpad, context manager, and approval gates. |
| `tests/test_wave_latency.py` | Eval harness measuring wave vs sequential execution speedup. |

### Router/Executor split

- Router model is selected by `SHIMS_ROUTER_MODEL` env var.
- If the model name starts with `claude-`, `gpt-`, or `gemini-`, the matching cloud provider is used.
- Otherwise the router defaults to an Ollama local model.
- `SHIMS_WAVE_ROUTER_SPLIT` controls the behavior:
  - `auto` (default): for Ollama, only use a separate router model if it is already loaded in `/api/ps`. This avoids paying a 60–180 s cold-start tax on consumer hardware.
  - `always`: force the split even if it triggers a model load.
  - `never`: use the executor model for planning too.

### Parallel execution

`execute_wave` runs non-duplicate calls concurrently via `asyncio.to_thread(...)` so even sync tools like `fs.read` or `shell.run` execute in parallel. Duplicate calls (same tool + same args) are skipped and reuse the first result.

### Approval gating

- Any call in a wave can return `{"needs_approval": True}`.
- If so, the entire wave stops and the agent loop yields an approval event.
- Post-approval, the pending action is re-run with `allow_gated=True`.

### Telemetry

Every wave and every tool call is recorded via `record_model_call()` in `shared/agent_loop.py` with latency, provider, model, success, and error. The UI surfaces these as live agent-telemetry cards.

### Adding a new tool

1. Add a `_run_*` function in `shared/agent_tools.py`.
2. Register it with `_register(Tool(...))`.
3. If the tool is async, wrap it in `asyncio.to_thread` or use an async runner inside the sync `_run_*` facade.
4. If the tool is risky, provide a `risk` callable that returns `"gated"`.

### Eval / regression

Run the wave latency harness:

```bash
.venv/Scripts/python tests/test_wave_latency.py
.venv/Scripts/python -m pytest tests/test_wave_latency.py -v -s
```

Expected baseline on healthy hardware:
- 3 equal mock tools: **~3x speedup** vs sequential.
- Mixed delays dominated by slowest: **~2x speedup**.

---

## Self-Modification Safety Model

SHIMS can edit its own source via `self.patch`. The pipeline is deliberately slow and safe:

1. **Propose** — LLM generates a diff (preferring Anthropic when configured; falls back to local Ollama coder model).
2. **Sandbox validate** — the patch is applied to a lean copy of the repo and `py_compile` / custom tests are run.
3. **Human approve** — the diff is shown in the UI; the user approves.
4. **Apply** — the patch is written to the live tree, re-validated, and rolled back automatically if validation fails.
5. **Archive** — the proposal is archived to `storage/evolution/archive/`.

Immutable harness files (`shared/self_evolver.py`, `shared/security.py`, `shared/config.py`) cannot be targeted.

### Skills

User preferences and learned behaviors are stored as JSON sidecars in `storage/skills/`. The agent injects the top relevant skills into the system prompt via `relevant_skills()` in `shared/skills.py`.

---

## Env vars agents should know

| Variable | Purpose |
|----------|---------|
| `SHIMS_ROUTER_MODEL` | Fast model for wave planning (e.g. `claude-sonnet-4-6`, `qwen2.5:3b`). |
| `SHIMS_WAVE_ROUTER_SPLIT` | `auto` / `always` / `never`. |
| `SHIMS_SELF_EVOLUTION_MODEL` | Local model for source rewrites (default `qwen2.5-coder:14b`). |
| `SHIMS_OMNIPOTENT_MODE` | If `true`, gates auto-apply and the agent loop is forced on for every turn; no permission prompts. |
| `SHIMS_FACTORY_MODEL` | Model used by the App Factory (`claude-sonnet-4-6` default; set `qwen2.5-coder:14b` for local Ollama). |
| `ANTHROPIC_API_KEY` | Cloud routing / rewrite provider key. |
| `OLLAMA_HOST` / default `127.0.0.1:11434` | Local model host. |
| `HUGGINGFACE_BASE_URL` / default `http://127.0.0.1:8080` | Local Hugging Face OpenAI-compatible endpoint (TGI / vLLM / llama.cpp server). |
| `HUGGINGFACE_API_KEY` | Optional bearer token for the HF endpoint. |
| `HUGGINGFACE_MODEL` | Default model ID, e.g. `meta-llama/Llama-3.1-8B-Instruct`. |
| `SHIMS_MEMORY_MODEL` | Local model for durable-fact extraction (default: preferred local model). |
| `SHIMS_PLANNER_MODEL` | Local model for plan DAG generation (default `qwen2.5:7b`). |
| `SHIMS_MUTATION_MODEL` | Local model for prompt-evolution mutations (default `qwen2.5:7b`). |
| `SHIMS_MAX_PARALLEL_TOOLS` | Max parallel tool calls per wave / across background jobs (default `4`). Lower if cloud providers rate-limit; raise on strong local hardware. |

---

## Coding conventions

- Python 3.11+ with `from __future__ import annotations`.
- Type hints encouraged; `dict[str, Any]` and `list[dict[str, Any]]` are common.
- Sync tool facades in `shared/agent_tools.py`; async work goes through `asyncio.to_thread` or explicit async helpers.
- Keep the `backend/app/main.py` monolith functional; prefer adding endpoints and small shared modules over deep refactors.
- Frontend is vanilla JS in `frontend/js/shims_omni.js`; keep it self-contained.

---

## Phase B — Skill Building & Self-Improvement

### B.1 Dynamic Skill Runtime

Skills are no longer limited to text memories. A skill can now be executable code:

* `runtime='text'` — injected into prompts (default).
* `runtime='tool'` — registers a new agent tool dynamically.
* `runtime='python'` — runs a sandboxed Python snippet.
* `runtime='jinja'` — renders a template into a prompt fragment.

**Files:**
- `shared/skill_runtime.py` — loads, registers, and executes skill plugins safely.
- `shared/skills.py` — extended schema supports `runtime`, `tool_schema`, `tool_code`.

**Tools:**
- `skill.learn` — save a text skill.
- `skill.create_tool` — turn a Python `run(args)` function into a live tool.
- `skill.execute` — run any skill by name or ID.
- `skill.list` — browse learned skills.

**Safety:** tool code is parsed with `ast`, imports/classes/non-`run` functions are rejected, and execution has a 5-second timeout.

### B.2 Prompt Evolution Lab

A/B test system-prompt variants against eval cases. Mutations are now generated by a cheap local LLM, with heuristic fallback. `prompt.run_eval` uses real prompt-quality cases (identity, tool instructions, safety, memory guidance, conciseness).

**Files:**
- `shared/prompt_evolution.py` — variants, runs, scoring, promotion, LLM mutation.
- `tests/test_prompt_evolution.py` — unit tests.

**Workflow:**
1. `ensure_control_variant(prompt_text)` creates the baseline.
2. `generate_mutations(parent, n=3)` creates children via `_llm_mutate_fn()`.
3. `run_eval_suite(variant_id, default_eval_cases())` scores a variant.
4. `promote_variant(variant_id)` makes it active.

**Tools:**
- `prompt.list_variants`
- `prompt.run_eval`
- `prompt.promote`

**Config:** set `SHIMS_MUTATION_MODEL` (default `qwen2.5:7b`).

### B.3 Background Coder Integration

Completed Coder projects can be folded back into the main SHIMS tree via the same `propose → validate → approve → apply` pipeline as `self.patch`.

**Files:**
- `shared/coder_bridge.py`

**Tool:**
- `coder.fold_project` — migrate a Coder project to a target directory.

### B.4 Browser + Mail Agent

A unified mail layer that prefers the Gmail API when OAuth is connected, and falls back to browser automation when the user is simply logged into Gmail on the desktop.

**Files:**
- `shared/mail_assistant.py`

**Tools:**
- `mail.assist.status` — detect available mail channel.
- `mail.assist.digest` — unified inbox digest.
- `mail.assist.compose` — send via API or browser compose URL.

### B.5 Evaluation-Driven Improvement Loop

Nightly/ondemand loop that runs reliability + wave-latency + prompt evals, reflects on failures, and proposes concrete improvements (new skill, new prompt variant, or self.patch).

**Files:**
- `shared/improvement_loop.py`

**Endpoints:**
- `POST /improvement/run`
- `GET /improvement/runs?limit=20`

**Tool:**
- `improvement.run_cycle`

---

## Phase C — Multimodal Agent Depth (Desktop AI better than Hermes)

### C.1 Vision Pipeline

Images attached in chat are described by the best available vision backend and prepended to the user message as context.

**Files:**
- `shared/vision.py`

**Backend priority:** Anthropic Claude → Ollama vision model (`llava`, `bakllava`, `moondream`, `llama3.2-vision`).

**Endpoints:**
- `POST /api/vision/describe`

**Tool:**
- `vision.describe`

### C.2 Code Interpreter in Chat

Python sandbox with automatic matplotlib capture and artifact collection. Useful for calculations, CSV/JSON analysis, and quick plots.

**Files:**
- `shared/code_interpreter.py`
- `shared/code_sandbox.py`

**Endpoints:**
- `POST /api/interpreter/run`
- `POST /api/interpreter/read`

**Tool:**
- `desktop.interpreter`

**Frontend:**
- Tool cards render embedded base64 PNG figures inline and list generated files.

### C.3 Vector Memory + Retrieval

The omni-brain memory/RAG layer stores `all-MiniLM-L6-v2` (384-dim) embeddings for every knowledge chunk and memory. Retrieval now does a hybrid blend of keyword/recency scoring plus cosine-similarity vector hits.

**Files:**
- `shared/omni_brain.py`

**Endpoints:**
- `POST /api/memory/save`
- `POST /api/memory/search`
- `POST /brain/reindex-vectors`

**Tools:**
- `memory.save`
- `memory.search`

**Backfill:** existing data without embeddings can be re-indexed via `POST /brain/reindex-vectors`.

### C.4 Long-Horizon Task Planner

Multi-step plans are persisted in SQLite, executed in dependency-resolved waves, and survive restarts. `plan_from_goal` now calls a cheap local LLM to generate a DAG of steps with `depends_on` edges, falling back to keyword splitting when the LLM is offline.

**Files:**
- `shared/desktop_planner.py`

**Endpoints:**
- `POST /api/plans`
- `GET /api/plans?status=&limit=20`
- `POST /api/plans/get`
- `POST /api/plans/cancel`

**Tools:**
- `plan.create`
- `plan.list`
- `plan.get`
- `plan.cancel`

**Config:** set `SHIMS_PLANNER_MODEL` to choose the planner model (default `qwen2.5:7b`).

### C.5 Desktop Automation & Scheduling

Cron-like scheduler with `once`, `interval`, and simple daily `cron` support. The scheduler polls every minute and runs lightweight tool or message actions. Registered on backend startup.

**Files:**
- `shared/desktop_scheduler.py`

**Endpoints:**
- `POST /api/schedule`
- `GET /api/schedule?enabled_only=false&limit=100`
- `POST /api/schedule/cancel`

**Tools:**
- `schedule.create`
- `schedule.list`
- `schedule.cancel`

**Runner registration** happens in `_register_scheduler_runners()` inside `backend/app/main.py` lifespan startup.


### C.4+ Plan Execution

Plans are no longer just data — they run. A wave executor (`shared/plan_executor.py`) routes each step to the right tool, passes prior-step output as input, and forces outputs into allowed scratch directories.

**Files:**
- `shared/plan_executor.py`

**Endpoints:**
- `POST /api/plans/run-wave`
- `POST /api/plans/run`

**Tools:**
- `plan.run_wave`
- `plan.run`

### C.5+ Auto-Memory

After every successful turn, durable facts are extracted by a cheap local LLM (plus fast regex heuristics) and saved into the omni-brain memory layer automatically. Extraction runs as a background task so it does not block chat streaming.

**Hook:** `_auto_memory_after_turn()` in `backend/app/main.py`.

**Config:** set `SHIMS_MEMORY_MODEL` to choose the extraction model (default: preferred local model).

### C.5+ Native Audio/Video/Screen Memory

Media files can be ingested into the omni-brain as searchable knowledge. Images/screenshots are described by the vision pipeline, audio is transcribed with faster-whisper, and videos are key-framed with ffmpeg + described frame-by-frame.

**Files:**
- `shared/media_memory.py`
- `shared/vision.py`

**Endpoints:**
- `POST /api/memory/ingest-media`

**Tools:**
- `memory.ingest_media`

### C.5+ Scheduler UI

A **Plans & Schedule** panel in the right sidebar shows active plans, upcoming scheduled tasks, and one-click run/cancel actions.

---

## Phase D — Agentic Polish

### D.1 Native Multimodal Chat

For Anthropic, OpenAI, Gemini, DeepSeek, and Kimi, attached images are passed as native content blocks instead of being pre-described. Ollama/local models still use the vision-description fallback.

**Files:**
- `shared/multimodal_messages.py`
- `backend/app/main.py` (`_build_user_message_with_images`)

### D.2 Auto-Planning Trigger

If the user message smells like a multi-step workflow (`"plan"`, `"step by step"`, `"workflow"`, `"automate"`, `"every day"`, multiple `and` clauses, etc.), SHIMS auto-creates a plan and streams wave execution instead of doing a single LLM turn.

**Trigger:** `_should_auto_plan()` in `backend/app/main.py`.

### D.3 Media Generation Tools

Image generation is exposed as a sync agent tool using Pollinations.ai (free, no API key). Video generation returns guidance to the async `/media/generate` endpoint.

**Files:**
- `shared/media_tools.py`

**Tools:**
- `media.generate_image`
- `media.generate_video`

### D.4 Mail Automation

Mail tools are registered for the agent loop: status probe, digest, compose, and organize (label/archive/delete). They use the Gmail API when OAuth is configured, otherwise browser automation.

**Tools:**
- `mail.status`
- `mail.digest`
- `mail.compose`
- `mail.organize`

---

## Phase E — ChemDFM Learning Sync

ChemDFM query, training fact recording, and journal/learning-gap analysis are exposed as agent tools and REST endpoints. Validated chemistry facts feed the iterative learning journal.

**Files:**
- `shared/chemdfm_bridge.py`

**Endpoints:**
- `POST /api/chem/chemdfm/query`
- `POST /api/chem/chemdfm/train`
- `GET /api/chem/chemdfm/journal?mode=summary|learn&limit=100`

**Tools:**
- `chem.chemdfm_query`
- `chem.chemdfm_train`
- `chem.chemdfm_journal`

---

## UI Polish Note

The right sidebar was redesigned to avoid crowding after adding Plans and Schedule panels:

- **Compact status strip** at the top: model, route, latency, live stage dots, agent roster popover.
- **Tabbed content**: Thinking | Plans | Feed.
- **CSS/JS**: `frontend/css/shims_omni.css`, `frontend/js/shims_omni.js`, `frontend/shims_omni.html`.

This keeps telemetry visible without overwhelming the user, and scales as more modules are added.

---

## Phase 3.1 — Self-Indexer (Omni "Soul, Brain & Swarm")

SHIMS can ingest its own allowed source tree into the omni-brain as searchable knowledge chunks, grounding coding questions in actual source.

**Files:**
- `shared/self_indexer.py`
- `tests/test_self_indexer.py`

**Endpoint:**
- `POST /api/brain/self-index?force=false`

**Tool:**
- `brain.self_index`

**Behavior:**
- Walks `ALLOWED_ROOTS` from `shared/self_evolver.py`.
- Skips `BLOCKED_PARTS`, immutable harness files, and unsupported extensions.
- Chunks Python (AST function/class), JS (function/class/const blocks), CSS (rulesets), and HTML (structural tags).
- Stores chunks via `shared/omni_brain.py` with `source_type='shims_source'`.
- Respects a 5-minute cooldown unless `force=true`.

---

## Phase 1 — Coder Mode Revival

The Omni-chat Coder slash commands and integrated Coder pane now hit working v2/v3 endpoints.

**Fixed endpoints (frontend → backend):**
- `GET /coder/v3/project/{id}/file?path=...`
- `POST /coder/v3/project/{id}/file`
- `DELETE /coder/v3/project/{id}/file`
- `POST /coder/v3/project/{id}/shell`
- `GET /coder/v3/project/{id}/search?query=...`
- `POST /coder/v2/project/{id}/git/commit`
- `POST /coder/v3/project/{id}/run`
- `POST /coder/v3/project/{id}/install`
- `POST /coder/v3/project/{id}/ai/iterate`
- `POST /coder/v3/project/{id}/ai/apply`

**Backend fixes:**
- `shared/coder_v2.py`: `_sanitize_python` regex uses proper `\b` word boundaries; `list_files()` supports `recursive=True`; `upload_folder()` accepts `list[int]` from JSON-serialized JS clients.
- `shared/coder_v3.py`: `ai_assist()` awaits the governor directly; new `ai_apply()` parses code blocks from an AI response and writes them via `write_file()`.

**Slash commands:** `/coder`, `/read-file`, `/write-file`, `/run-shell`, `/run-project`, `/search`, `/install`, `/git-commit`.

---

## Phase 2 — Swarm Runtime

Omni can dispatch multiple specialist agents in parallel and synthesize a unified answer.

**Files:**
- `shared/swarm_orchestrator.py` — real meta-orchestrator: analyzes the prompt, builds a dependency-aware plan, and runs coder/reviewer/tester/researcher agents in waves with a shared scratchpad.
- `shared/swarm_runtime.py` — real async agent-loop dispatcher.
- `shared/swarm.py` — deterministic offline synthesizer (no LLM required).
- `tests/test_swarm_orchestrator.py`, `tests/test_swarm_runtime.py`

**Endpoint:**
- `POST /agent/swarm`:
  - `orchestrate=true` (default) → meta-orchestrator with plan → code → review → test → synthesize.
  - `use_llm=true` + `orchestrate=false` → legacy `SwarmDispatcher` agent-loop swarm.
  - `use_llm=false` → instant deterministic offline synthesis.

**Tool:**
- `agent.swarm`

**Frontend:**
- `/swarm <task>` slash command now uses the orchestrator by default and renders a live agent activity log.

### Orchestrator behavior

1. **Planner agent** analyzes the prompt and emits a JSON plan of subtasks with dependencies. Falls back to a deterministic plan if the LLM is unavailable.
2. **Coder agent** creates a Coder v2/v3 project, generates files, syntax-checks, runs the project, runs tests, and rewrites files on failure (up to a max iteration budget).
3. **Reviewer agent** reads the generated files and produces a concise review.
4. **Tester agent** runs the project test suite.
5. **Researcher agent** searches the web when the task mentions APIs, libraries, or external tools.
6. **Synthesis** combines all outputs into a final answer with project IDs and file lists.

---

## Phase 3.2 — Real Improvement Loop

The evaluation-driven improvement loop now uses real prompt-quality eval cases, performs LLM-based root-cause reflection, and produces concrete, safe proposals (never auto-applies code).

**Files:**
- `shared/improvement_loop.py`
- `tests/test_improvement_loop_real.py`

**Endpoints:**
- `POST /improvement/run`
- `GET /improvement/runs`

**Tool:**
- `improvement.run_cycle`

**Proposal types:**
- `self.patch` proposals for allowed source targets (immutable harness files rejected).
- New skills via `shared/skills.save_skill`.
- Prompt-variant mutations via `shared/prompt_evolution.generate_mutations`.

**Config:** set `SHIMS_IMPROVEMENT_MODEL` to override the reflection model (default `qwen2.5:7b`).

---

## Phase 4 — Power Expansion

### MCP Client

`shared/mcp_registry.py` is now a real MCP JSON-RPC client, so Omni can call external tool servers.

**Files:**
- `shared/mcp_registry.py`
- `tests/test_mcp_client.py`

**Tools:**
- `mcp.list_servers`
- `mcp.call_tool`

**Config:** `storage/mcp_servers.json`.

### Cloud Provider Wiring

`shared/agent_loop.py` now has real `_openai_chat_raw` and `_google_chat_raw` transports, plus a generic `_openai_compatible_chat_raw` / `_openai_compatible_chat_stream` for Kimi, DeepSeek, and Qwen. The fallback chain Anthropic → OpenAI → Google → Kimi/DeepSeek/Qwen → Ollama works end-to-end, and the LLM gateway routes these providers through the same transports.

### Agentic Plan Executor

`shared/plan_executor.py` routes `agent.run` steps through the wave engine instead of brittle keyword regex, and retries failed direct-tool steps with exponential backoff.

---

## v19 — SHIMS Omni App Factory

SHIMS Omni can generate and host self-contained vertical apps under `apps/<app_name>/`.
The canonical example included in the public repo is `apps/todo_demo/`.

### New app structure
- `apps/<app_name>/app.py` — FastAPI router factory (`create_*_router()` + `mount_static()`).
- `apps/<app_name>/database.py` — self-contained SQLite schema, WAL mode, connection helpers.
- `apps/<app_name>/config.py` — paths, default users/roles, AI model selection.
- `apps/<app_name>/services/` — pure Python domain modules.
- `apps/<app_name>/templates/`, `static/` — vanilla JS + CSS frontend.
- Mount in `backend/app/main.py` with `app.include_router(...)` and static mount.
- Add launcher tile in `frontend/shims_omni.html`.

### Todo Demo app capabilities
- Simple task management: create, list, complete, and delete todos.
- SQLite-backed persistence under `storage/todo_demo.sqlite3`.
- Auth-aware routes and a vanilla JS frontend.

### Running the Todo Demo app
1. Start the main SHIMS backend.
2. Open SHIMS Omni and click **Todo Demo** in the left modules panel, or navigate to `http://<host>/todo`.

### Tests
- `tests/test_todo_demo.py` covers the task CRUD flow end-to-end.

### Extending the factory
- Copy `apps/todo_demo/` to `apps/<new_app>/`.
- Rename router function, mount prefix, static mount, and launcher tile.
- Update `config.py` default users/roles.
- Add domain services and templates.
- Add `tests/test_<new_app>.py`.

## App Doctor (self-diagnose / self-repair)

SHIMS can now inspect and fix common vertical-app bugs on its own.

**Files:**
- `shared/app_doctor.py` — `diagnose_app(app_name)` and `repair_app(app_name)`.
- `shared/agent_tools.py` — `app_factory.diagnose_app` / `app_factory.repair_app`.
- `backend/app/main.py` — `POST /api/app-factory/diagnose`, `POST /api/app-factory/repair`.

**What it checks:**
1. Static file mount path in `backend/app/main.py` matches references in templates/JS/CSS.
2. An `auth` router exists when `DEFAULT_ROLES` are configured.
3. The auth router is actually wired into `apps/<name>/app.py`.
4. App-specific pytest suite passes.

**What it auto-fixes:**
- Rewrites wrong `-static/` URLs to the real mount path.
- Generates a minimal `routers/auth.py` if missing.
- Wires the auth router into `app.py`.

**Usage from chat:**
- "Diagnose the Todo Demo app" → runs `app_factory.diagnose_app`.
- "Repair the Todo Demo app" → runs `app_factory.repair_app` (safe, app-directory only).

**Usage from UI/scripts:**
- `POST /api/app-factory/diagnose {"app_name":"todo_demo"}`
- `POST /api/app-factory/repair {"app_name":"todo_demo"}`

## Consolidation note

What was consolidated in the current build:
- Removed the broken Coder pane from `frontend/shims_omni.html` / `frontend/js/shims_omni.js`; coding powers remain in the main chat via agent tools and slash commands.
- Added `shared/app_doctor.py` + `app_factory.diagnose_app` / `app_factory.repair_app` tools for self-diagnosis.
- Cleaned `.venv.bak` and `storage/sandbox/validate_patch_*` temp copies.

---

## Local Factory Instance (Instance B)

SHIMS can run a second, isolated **Local Factory** instance on the same machine for fully offline Ollama workloads.

### Layout

- **Instance A** (port 8010): main Omni stack, default cloud-backed.
- **Instance B** (port 8030): isolated local stack using Ollama (`qwen2.5:3b`, `qwen2.5:7b`, `chemdfm`).
- **Storage**: `SHIMS_STORAGE_DIR` for Instance B should point to `storage_local/` so it does not mix with Instance A data.
- **Config**: `config/peers.json` lists both instances and the shared `INTER_INSTANCE_TOKEN` used to authenticate peer requests.

### Key files

| File | Purpose |
|------|---------|
| `shared/local_factory_config.py` | Model/storage resolution for Instance B. |
| `shared/local_factory_corpus.py` | Builds BMR/chemistry/web training corpus. |
| `shared/factory_evolution_loop.py` | Overnight corpus → train → benchmark → promote → propose loop. |
| `shared/factory_routes.py` | FastAPI router mounted at `/api/factory`. |
| `shared/inter_instance_bridge.py` | Peer auth, `PeerClient`, and `/api/peer/*` routes. |
| `scripts/start_shims_local_factory.py` | Launcher that forces `.env.local` and Instance B env overrides. |
| `scripts/train_local_factory_model.py` | Entry point: `ollama` persona, `peft` LoRA, or `export`. |
| `tests/test_local_factory.py` | Unit + live integration tests. |

### Running Instance B

```bash
. .env.local && SHIMS_INSTANCE_ID=local SHIMS_ENV_FILE=.env.local SHIMS_PEERS_FILE=config/peers.json .venv/Scripts/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8030 --no-access-log
```

Or use the wrapper:

```bash
.venv/Scripts/python scripts/start_shims_local_factory.py
```

### Peer auth

Both instances must agree on the inter-instance token. The precedence is:

1. `INTER_INSTANCE_TOKEN` env var (highest)
2. `settings.bridge_token` from the active env file
3. `config/peers.json` `token` (used by `PeerClient` as a fallback)

**Do not use the default placeholder in production.** Set a strong `INTER_INSTANCE_TOKEN` in both `.env` and `.env.local` and update `config/peers.json`.

### Factory endpoints

On Instance B:

- `GET /api/factory/status`
- `POST /api/factory/corpus/build`
- `GET /api/factory/corpus/stats`
- `POST /api/factory/corpus/sync-peer`
- `POST /api/factory/evolution/run`

On Instance A the same routes return a note pointing at Instance B.

### Peer tools

Instance A can call tools on Instance B via:

- `peer.status`
- `peer.call`
- `peer.sync_corpus`
- `local_llm.chat`
- `factory.corpus_stats`
- `factory.build_corpus`
- `factory.train_model`
- `factory.run_evolution`

Whitelisted peer tools include `brain.search`, `memory.search`, `chem.chemdfm_query`, `local_llm.chat`, etc. Risky tools (shell, file writes, self-evolution) are intentionally blocked across peers.

### Validation

```bash
.venv/Scripts/python -m pytest tests/test_local_factory.py -v
```

Set `SKIP_LIVE_FACTORY=1` to skip tests that require the Instance B server.

---

## Omni DuoBot — Council of the Wises

The DuoBot is now a **Council of the Wises**: up to five agents (Omni, Gemini, Claude, OpenAI, and the local Factory) discuss each turn, a Chair agent synthesizes a final decision, and the council can execute gated tools with user approval (or auto-execute when enabled). The council is not limited to SHIMS self-improvement — it can deliberate on any plan, use case, or question the user asks. The UI is galaxy-themed with a pulsating orb and modern side panels.

### Key files

| File | Purpose |
|------|---------|
| `shared/omni_duobot.py` | Conversation engine, council orchestration, RAG feeding, proposal aggregation, voting, apply. |
| `shared/duobot_routes.py` | FastAPI REST router (`/api/duobot/*`). |
| `frontend/omni_duobot.html` + `frontend/js/omni_duobot.js` | Galaxy-themed Council UI with API-key inputs and curated model selectors. |

### Modes

- `free` — chat between all enabled council members.
- `improvement` — DuoBot runs improvement-driven turns and surfaces proposals.
- `council` — full Council of the Wises with Chair decisions and tool execution. Open one from Omni chat with `/council <task>`.

You can also open a council directly on any topic via URL: `/omni-duobot?mode=council&topic=Plan+a+new+app+factory+workflow`.

### Endpoints

- `GET /omni-duobot` — Council UI.
- `POST /api/duobot/conversations` — create conversation (`mode` optional).
- `GET /api/duobot/conversations` — list conversations.
- `GET /api/duobot/conversations/{id}` — get messages + votes.
- `POST /api/duobot/conversations/{id}/message` — user authoritative input.
- `POST /api/duobot/conversations/{id}/turn` — run one council turn.
- `POST /api/duobot/conversations/{id}/mode` — switch mode.
- `POST /api/duobot/conversations/{id}/finalize` — produce final summary.
- `POST /api/duobot/conversations/{id}/council/approve` — approve a gated council action.
- `POST /api/duobot/conversations/{id}/council/reject` — reject a gated council action.
- `GET /api/duobot/proposals` — pending proposals from both instances.
- `POST /api/duobot/proposals/{id}/vote` — approve/reject.
- `POST /api/duobot/proposals/{id}/apply` — apply an approved proposal.
- `POST /api/duobot/proposals/{id}/delete` — permanently delete a proposal.
- `POST /api/duobot/proposals/{id}/rethink` — reject, delete, and queue feedback for an alternative.
- `GET/POST /api/duobot/settings/ai` — AI settings including per-council-member overrides and RAG.

### Council members

Default members and their API keys:

| Member | Provider | Default model | Key env |
|--------|----------|---------------|---------|
| Omni | primary (Kimi) | `kimi-k2.6` | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` if routed |
| Gemini | Google | `gemini-2.5-flash` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| Claude | Anthropic | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenAI | OpenAI | `gpt-4o-mini` | `OPENAI_API_KEY` |
| Factory | Ollama | `qwen2.5-coder:14b` | local |

Each member can be enabled/disabled and given a custom provider, model, temperature, and system prompt in **Settings → Council Personas**. The default prompts are task-agnostic — the council answers general questions directly and only routes to SHIMS tools when the request clearly involves SHIMS code, files, or desktop/server actions.

### RAG and token savings

SHIMS source context is retrieved **once per council turn** by Omni and added as a single shared `context` message. Every council member sees it in the chat history, so agents stay chat-aware without each one repeating the long retrieval. Toggle this with **Settings → Feed SHIMS source context to council**.

### API keys and model picker

The **Settings → API Keys** tab lets you paste keys for Anthropic, OpenAI, and Gemini, pick a curated quality model, and test the key in one click. Model inputs use curated datalists so the default is never the cheapest/basic tier (e.g. `gpt-4o`, `gemini-2.5-pro`, `claude-sonnet-4-6`). Per-council-member overrides also get the same curated model suggestions.

### Rich improvement proposals

Proposals now carry:

- **Why this proposal**
- **Problem statement**
- **Solution proposed**
- **Options considered**
- **Files to change**
- **Expected benefit**
- **Risk**

Users can **Approve**, **Reject**, **Reject & Rethink** (with feedback), or **Reject & Delete**.

### Safety

- Gated tools (`fs.write`, `self.patch`, etc.) require explicit user approval unless `SHIMS_OMNIPOTENT_MODE=true` or **Council auto-execute** is enabled in settings.
- The Chair only proposes actions; execution goes through the same `agent_tools.run_tool` gating used by the main agent loop.

### Process manager

Use `scripts/shims_process_manager.py` to start/stop/restart both instances and a dedicated Ollama server on port 11435:

```bash
.venv/Scripts/python scripts/shims_process_manager.py start
.venv/Scripts/python scripts/shims_process_manager.py status
.venv/Scripts/python scripts/shims_process_manager.py restart
.venv/Scripts/python scripts/shims_process_manager.py stop
```

The manager writes PIDs to `storage/process_manager/pids.json` and can be run with `monitor` to auto-restart dead services.

### Loop protection

- **Hard message cap** — `SHIMS_DUOBOT_MAX_MESSAGES` (default 100).
- **Exact-duplicate detection** — refuses to post the same message twice within the lookback window.
- **Similarity guard** — word-overlap (Jaccard) check blocks near-duplicate echoing.
- **Auto-run stop** — the frontend stops auto-run if the backend returns a stuck/duplicate error.
- **History window** — only the last 10 messages are sent to the model so it stays focused.

### Validation

```bash
.venv/Scripts/python -m pytest tests/test_duobot_council.py tests/test_duobot_tasks.py -v
```
