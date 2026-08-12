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
| `SHIMS_ROUTER_MODEL` | Fast model for wave planning (native GGUF id, or a cloud model name). |
| `SHIMS_WAVE_ROUTER_SPLIT` | `auto` / `always` / `never`. |
| `SHIMS_SELF_EVOLUTION_MODEL` | Local model for source rewrites (native GGUF id; default = loaded model). |
| `SHIMS_OMNIPOTENT_MODE` | If `true`, gates auto-apply and the agent loop is forced on for every turn; no permission prompts. |
| `SHIMS_FACTORY_MODEL` | Model used by the App Factory (`claude-sonnet-4-6` default; set a native GGUF id for local). |
| `ANTHROPIC_API_KEY` | Cloud routing / rewrite provider key. |
| `OLLAMA_HOST` / default `127.0.0.1:11434` | **Legacy / Instance B only.** SHIMS Instance A is native-only: Ollama is never probed or routed to by default anywhere (chat, feature models, gateway, fallback chains). See "Native-only routing" below. |
| `HUGGINGFACE_BASE_URL` / default `http://127.0.0.1:8080` | Local Hugging Face OpenAI-compatible endpoint (TGI / vLLM / llama.cpp server). |
| `HUGGINGFACE_API_KEY` | Optional bearer token for the HF endpoint. |
| `HUGGINGFACE_MODEL` | Default model ID, e.g. `meta-llama/Llama-3.1-8B-Instruct`. |
| `SHIMS_MEMORY_MODEL` | Native GGUF id for durable-fact extraction (default: currently loaded native model). |
| `SHIMS_PLANNER_MODEL` | Native GGUF id for plan DAG generation (default: loaded model). |
| `SHIMS_MUTATION_MODEL` | Native GGUF id for prompt-evolution mutations (default: loaded model). |
| `SHIMS_FEATURE_LLM_TIMEOUT_S` | Timeout for `shared/local_llm.feature_chat` feature calls (default 180). |
| `SHIMS_MAX_PARALLEL_TOOLS` | Max parallel tool calls per wave / across background jobs (default `4`). Lower if cloud providers rate-limit; raise on strong local hardware. |
| `SHIMS_CHAT_PROVIDER` / `SHIMS_CHAT_MODEL` | Brain-model override saved from Settings → Brain Model; the backend is the source of truth (frontend adopts it on load). Local values resolve to the native engine; cloud providers are honored explicitly. |
| `KOBOLDCPP_BASE_URL` / `KOBOLDCPP_MODEL` | KoboldCPP OpenAI-compatible endpoint (default `http://127.0.0.1:5001`) and model id. Same pattern for `VLLM_*`, `SGLANG_*`, `APHRODITE_*`. |
| `SHIMS_NATIVE_*` | Native engine: `SHIMS_NATIVE_MODEL` (GGUF pick), `SHIMS_NATIVE_CTX` (default 16384; explicit pin always wins), `SHIMS_NATIVE_GPU_LAYERS`, `SHIMS_NATIVE_THREADS`, `SHIMS_NATIVE_BACKEND` (`vulkan`/`cuda`/`cpu`), `SHIMS_NATIVE_PORT` (default 5115), `SHIMS_NATIVE_RUNTIME` (exe override), `SHIMS_NATIVE_IDLE_UNLOAD_MIN` (0 = never unload), `SHIMS_NATIVE_PARALLEL` (parallel request slots, default 4), `SHIMS_NATIVE_TIMEOUT` (per-request socket timeout, default 900 — must cover slot-queue waits), `SHIMS_NATIVE_SMARTCACHE` (KV snapshot limit for cross-turn prompt caching, default 8 — kills the per-turn full-prompt re-eval). |
| `SHIMS_CHAT_CTX` | Constant context length sent to local providers (default 8192) — prevents per-turn model reloads keyed to ctx size. |
| `SHIMS_CHAT_MAX_TOKENS` | Output cap per chat turn. **Unset (default) = unlimited** (ceiling = full context window; the UI stop button is the kill switch). Set an integer to reinstate a hard cap. `SHIMS_REASONING_TOKEN_RESERVE` (default 0) is legacy headroom for capped setups only. |
| `SHIMS_KEEP_WARM` | `true` (default): background 4-min heartbeat keeps the active local model loaded. |
| `SHIMS_RETRIEVAL_BUDGET_S` | Max seconds a chat turn waits for RAG retrieval (default 1.5). |
| `SHIMS_RAG_STRONG_SCORE` / `SHIMS_VECTOR_STRONG_SIM` / `SHIMS_RAG_COVERAGE_MIN` | Relevance admission for chat RAG: keyword hits need score ≥ 3.0 (default) AND, for multi-token queries, ≥ 2 distinct matched content tokens (exact-phrase matches always pass); vector hits need sim ≥ 0.70. Set the strong vars ≤ the `*_MIN_*` vars (and coverage to 1) to restore lenient behavior. |
| `SHIMS_NIGHTLY_MODEL` / `SHIMS_NIGHTLY_TIMEOUT_S` | Big native GGUF id for the nightly self-fix reflection (default: loaded model) and its timeout (default 1800 s — 100B-class GGUFs are slow, that's fine at 1 AM). |
| `SHIMS_NIGHTLY_CRON` / `SHIMS_NIGHTLY_AUTO_APPLY` | Nightly loop schedule (daily "M H * * *", default `0 1 * * *` = 1:00 AM local) and whether low-risk patch proposals auto-apply through validate→approve→apply with rollback (default `true`; riskier proposals wait for morning approval). |
| `SHIMS_OBSERVER_INTERVAL_MIN` | Minutes between day-observer status snapshots (default 30). |
| `SHIMS_SECONDARY_PROVIDERS` | How auto-routing may use non-native local backends. `off` (default) never probes them — strictly native + cloud. `auto` probes LM Studio/Ollama/vLLM/SGLang/Aphrodite/KoboldCPP only when the native engine has no model loaded (legacy); `all` or a comma list always allows them. |
| `SHIMS_MODEL_DOWNLOAD_DIR` | Where GGUF downloads land. Defaults to `~/.lmstudio/models/shims` when an LM Studio library exists, so native and LM Studio share one copy of every model. The `shims/` subdir is what `managed_dirs()` trusts, so SHIMS can delete what it downloaded but never the user's pre-existing LM Studio models. |
| `SHIMS_REASONING_EFFORT` | `auto` (default) \| `none` \| `low` \| `medium` \| `high`. Under `auto` the lite lane sends `none` and the tool-capable lane omits the field so a thinking model thinks. Set explicitly to force one value on both lanes. `none`/`low` also pass `chat_template_kwargs.enable_thinking=false` to the native engine (verified: kills the reasoning block entirely, so Qwen3.x thinking models reply in seconds instead of starving the output budget). |
| `SHIMS_LITE_LANE` | `trivial` (default): only greetings/acks take the no-tools fast lane. `broad` restores the old substring blocklist — a latency-triage rollback only; it costs the model its tools on any turn whose wording misses the list. |
| `SHIMS_RAG_MIN_SCORE` / `SHIMS_VECTOR_MIN_SIM` | Relevance gates for keyword (default 1.5) and vector (default 0.62) retrieval hits. |
| `SHIMS_APPROVAL_TTL_S` | Pending approvals expire after this many seconds (default 1800) and are session-scoped — a bare "yes" can never execute a stale/cross-session approval. |
| `SHIMS_IDLE_MIN` / `SHIMS_COMFYUI_RESERVE_GB` / `SHIMS_FISH_IDLE_MIN` / `SHIMS_FISH_RESERVE_GB` | Compute-orchestrator policy: idle threshold (default 5 min) before downtime image drains, ComfyUI reserve (12 GB), fish-speech idle-stop (15 min) and reserve (8 GB). |
| `SHIMS_CHAT_TEMPERATURE` | Brain Controls: default chat temperature (default 0.2). |
| `SHIMS_TOOL_SCOPING` | `on` (default): obvious single-domain turns ship only that domain's tool schemas. `off` restores the full set every turn. |
| `SHIMS_DIGEST_INTERVAL_MIN` | Minutes between scheduled comms digests (default 120). |

---

## Native Engine & Compute Orchestrator

SHIMS has its own GGUF inference engine — no LM Studio/Ollama needed in the default chat path. It embeds the koboldcpp runtime (`tools/koboldcpp/`) as a SHIMS-owned child process with SHIMS-computed flags, auto-tuned from live hardware probing (works on any machine: NVIDIA→CUDA, AMD/unified→Vulkan, else CPU).

### Files

| File | Purpose |
|------|---------|
| `shared/native_engine/discovery.py` | GGUF discovery (`storage/models/`, `data/models/`, `~/.shims/models`, read-only LM Studio dir) + pure-Python GGUF header parser. |
| `shared/native_engine/tuning.py` | `compute_launch_plan()` → gpu_layers/ctx/threads/backend/parallel_slots from `hardware_profiler` + psutil free RAM. Slots default to `SHIMS_NATIVE_PARALLEL` (4); total ctx scales to `SHIMS_CHAT_CTX × slots` unless `SHIMS_NATIVE_CTX` is pinned, and slots auto-degrade when the memory budget caps ctx. |
| `shared/native_engine/runtime.py` | Runtime spawn/health/watchdog/kill; flags derived from the binary's `--help`. Always adds `--jinja_tools` (or `--jinja`) when advertised — without it the server silently drops the `tools` payload and the model can never emit `tool_calls` — and `--parallelrequests N` from the plan's `parallel_slots`. |
| `shared/native_engine/engine.py` | `NativeEngine` singleton: lifecycle + `chat_raw`/`chat_stream` (OpenAI-compatible loopback, default port 5115). |
| `shared/native_engine/budget.py` | Memory ledger + idle-unload policy consumed by the orchestrator. |
| `shared/compute_orchestrator.py` | One authority over whole-machine memory: budget ledger, idle state machine, downtime ComfyUI image queue, fish-speech-2 server lifecycle. |

### Provider `native`

Registered in `shared/agent_loop.py` (`_native_chat_raw`/`_native_chat_stream`, streaming set, dynamic fallback-chain prepend only when healthy), `shared/llm_gateway.py`, `shared/ai.py` (`NativeProvider`), `shared/agent_model_router.py`. Auto-starts at backend lifespan when a GGUF exists; `_resolve_provider_model` prefers it (zero HTTP fan-out). Endpoints: `GET /api/native-engine/status`, `POST /api/native-engine/load`, `POST /api/native-engine/unload`.

### Orchestrator

Ticks every 30 s from the backend lifespan. When idle ≥ `SHIMS_IDLE_MIN` with queued image jobs, it launches/locates ComfyUI and drains the queue; on new chat activity it finishes the in-flight job then suspends. It starts fish-speech-2 on first TTS request and idle-stops it. It never kills servers it didn't start. Endpoints: `GET /api/orchestrator/status`, `POST /api/orchestrator/image-job`, `GET /api/orchestrator/image-job/{id}`. Video generation slots into the same budget/suspend design later.

### Desktop hub & inbound channels

- **Desktop Hub canonical source lives in the repo at `hub/`**
  (`live_shims_hub.py`, `live.html`, `live-app.js`, `live-style.css`).
  `scripts/install_hub.bat` deploys it to the Desktop install dir
  (`~/Desktop/00_SHIMS_DESKTOP_HUB/`) and restarts it. The hub is
  **stdlib-only** by requirement: the launcher starts it with whatever
  `python` is on PATH, not the venv — no third-party imports, ever. It owns
  no state; every panel degrades independently, and it falls back to the next
  free port rather than exiting on bind failure. Its open-items taskboard
  prefers SHIMS's live digest (`data/state/taskboard.json`) and falls back to
  the local static `open-items-data.json` when no digest has run.
- **Inbound channels** (`shared/channels.py`, `/api/channels/inbound`,
  `/api/channels/{channel}/recent`) are the landing zone for message bridges.
  Append-only, bounded retention, `(channel, message_id)` unique so a retried
  relay or a WhatsApp redelivery is a no-op. Token-gated on `SHIMS_BRIDGE_TOKEN`
  and **fails closed when unset** — it ingests message content from another
  process. Inbound only: sending would mean acting as the user toward third
  parties and needs an explicit approval path, not an HTTP endpoint.
- **WhatsApp** arrives via `integrations/whatsapp/index.js`, a SHIMS-owned
  baileys sidecar (no OpenClaw involvement) that posts inbound messages to
  `/api/channels/inbound`. Sidecar endpoints: `:5116/status`, `/qr`,
  `/qr/text`, `/logout`. Groups are opt-in via `SHIMS_WHATSAPP_GROUPS=true`.
  The legacy OpenClaw relay (`integrations/openclaw/shims-channel-bridge`)
  remains as an alternative but is no longer the default path.
- **Comms digest & taskboard** (`shared/comms_digest.py`) runs on the
  scheduler (`comms_digest` action, default every `SHIMS_DIGEST_INTERVAL_MIN`
  = 120 min) and on demand (`POST /api/taskboard/run`, `comms.digest` tool).
  It classifies recent Gmail + WhatsApp into Urgent / Needs reply / Waiting /
  FYI — one scoped native LLM turn, keyword fallback — and writes
  `data/state/taskboard.json` for the hub and `GET /api/taskboard`. Items
  carry deep links: Gmail threads (`/u/1/#all/<thread_id>` — the user's
  second Google account) and WhatsApp DMs (`wa.me/<number>`, digit
  senders only). Manual pins (`taskboard_pins.json`, `add_pin()`) survive
  regeneration and float to the top.
- **Vendor inventory** (`shared/chat_inventory.py`) rides the digest cadence:
  a vendor-wise index of products/raw materials, equipment, and services
  offered on WhatsApp + Gmail, with quoted rates (`₹/Rs/USD/lakh` patterns),
  evidence snippets, and deep links. Endpoints: `GET /api/inventory`,
  `POST /api/inventory/run`, `GET /api/inventory/export.xlsx` (4-sheet
  openpyxl workbook); chat tool `inventory.export` returns a downloadable
  `/media/files/exports/*.xlsx` link. The hub renders it as the Vendor
  Inventory panel, including a **My Quoted Rates** tab for outbound quotes —
  the WhatsApp sidecar relays `fromMe` messages (`metadata.is_mine`) and
  Gmail is scanned with `in:sent`, so rates the user quotes land there.
  `_llm_enrich` (env `SHIMS_INVENTORY_LLM`, default on) refines item/rate/qty
  rows per vendor with a native LLM turn when the engine is free.
- **Gmail** uses the OAuth flow in `shared/mailbox.py`
  (`/mailbox/oauth/start` → `/mailbox/oauth/callback` → `/mailbox/gmail/sync`).
  Default scopes are `gmail.readonly` + `gmail.compose` — full read access and
  draft creation. SHIMS **never sends email directly**; the `mail.draft` chat
  tool creates drafts the user reviews and sends manually. The `mail.read` tool
  lists/searches/reads messages, and `mail.attachment` downloads attachments to
  `data/downloads/`. Override scopes with `SHIMS_GMAIL_SCOPES`. Needs
  `SHIMS_GMAIL_CLIENT_ID`/`_SECRET` from a Google Cloud OAuth client — the
  consent step is the user's, never automated.

### Chat-latency architecture notes

- Both chat lanes send a constant `SHIMS_CHAT_CTX` and stream true SSE (no buffered pseudo-stream). Reasoning effort comes from `_reasoning_effort()`, not a hardcoded value: the lite lane suppresses thinking for latency, the full lane omits the field so a thinking model reasons at its own default.
- System prompts use a **stable prefix** (identity/directives first, byte-constant) with all per-turn content (RAG addendum, lessons, feedback prefs) in a trailing `[Live context]` block — this keeps llama.cpp-family KV prefix caching effective across turns.
- **Tool-calling contract.** `TOOL_CALLING_PROVIDERS` in `backend/app/main.py` is the single source of truth for "may the brain model call a tool". A provider belongs there only if its `_model_turn_events` branch both forwards `tools` and parses `tool_calls` back out — listing one that does not silently drops every call, and omitting one that does leaves the model with no tools at all. Both failure modes look identical to the user: confident, unsourced answers. The curated schema (`_unified_chat_tools`) covers `web.search`, `web.fetch`, `media.create`, `agent.spawn`, `agent.assign` (background specialists), `desktop.bridge`, `mail.read`, `mail.draft`, `mail.attachment`, `channels.recent` (read-only inbound WhatsApp/channel messages), `comms.digest` (classified taskboard digest), `skill.*`; execution and the trust/ledger envelope live in `_execute_unified_tool`. Chat turns ship only the matched domain's subset (`shared/agent_domains.scoped_tools`); ambiguous turns get the full set. Each call emits `tool_call`/`tool_result` events so the UI renders an inspectable tool card.
- Retrieval is skipped for simple/tool-intent/search-intent turns; vector scans are `LIMIT`-bounded; `omni_feedback` and pinned core memories never count as RAG evidence; the MEMORY-BACKED trust card requires a genuine non-core hit above threshold.
- Smoke test: `.venv/Scripts/python scripts/native_engine_smoke.py`; TTFT harness: `.venv/Scripts/python scripts/time_first_token.py`.

### Validation

```bash
.venv/Scripts/python -m pytest tests/test_native_engine.py tests/test_compute_orchestrator.py tests/test_approval_guard.py tests/test_omni_brain_relevance.py -q --basetemp=.pytest_tmp
```

---

## Native-only routing & day observer / nightly loop

**Native-only local inference.** SHIMS Instance A routes ALL local LLM work —
chat, wave planning, auto-memory, planner, prompt mutation, improvement
reflection — through the native GGUF engine. `_resolve_provider_model`
(`backend/app/main.py`) maps every local provider alias (`auto`, `local`,
`ollama`, `lmstudio`, `vllm`, `sglang`, `aphrodite`, `koboldcpp`,
`huggingface`) to native and never probes secondary backends; explicit cloud
providers are still honored. `shared/local_llm.feature_chat()` is the single
helper feature code uses for background LLM calls (native loopback, port
`SHIMS_NATIVE_PORT`; returns `""` on any failure so heuristic fallbacks fire).
Ollama-based code remains only for the isolated Instance B (Local Factory).
The user's Settings → Brain Model pick is the source of truth: the backend
persists it (`SHIMS_CHAT_PROVIDER`/`SHIMS_CHAT_MODEL`), the frontend adopts it
on load, and the resolver/engine lock honor it over "whatever GGUF is loaded".

**Day observer** (`shared/day_observer.py`): a scheduler job (`day_observer`
action, every `SHIMS_OBSERVER_INTERVAL_MIN` = 30 min) appends status snapshots
to `logs/observer/YYYY-MM-DD.jsonl`; `collect_day_report()` rolls the day's
episodes, telemetry errors/latency, tool/model-call health, thumbs feedback,
and ledger state into `logs/observer/YYYY-MM-DD-report.md`.

**Nightly loop** (`shared/nightly_loop.py`): a scheduler cron job
(`nightly_cycle` action, `SHIMS_NIGHTLY_CRON` = `0 1 * * *`) runs
`run_nightly_cycle()`: day report → app-doctor pass over `apps/` →
`run_improvement_cycle(extra_context=…, auto_apply_low_risk=…)`. The
reflection LLM is the big native GGUF (`SHIMS_NIGHTLY_MODEL`, timeout
`SHIMS_NIGHTLY_TIMEOUT_S` = 1800 s). With `SHIMS_NIGHTLY_AUTO_APPLY=true`
(default), low-risk patch proposals auto-apply through the normal
validate→approve→apply pipeline (auto-rollback on failure); riskier proposals
wait for morning approval. Endpoints: `POST /api/nightly/run`,
`GET /api/nightly/runs`, `GET /api/observer/today`. Tests:
`tests/test_nightly_loop.py`.

---

## Enterprise bridge (SHIMS ↔ SHIMS Enterprise)

SHIMS connects to the standalone SHIMS Enterprise app (ERP/MES/LIMS/QMS/DMS/RIM,
default `http://127.0.0.1:8020`, code at `C:/d/shims_enterprise_local`) with
full awareness and record-level CRUD.

- **Client**: `shared/enterprise_bridge.py` — `bridge_command()`,
  `enterprise_get/post()`, `status()`, `awareness_snapshot()`,
  `sync_brain_awareness()`. Auth: `X-Bridge-Token` = `SHIMS_BRIDGE_TOKEN`
  (same value on both sides; `ENTERPRISE_BRIDGE_TOKEN` accepted as fallback).
  Enable with `SHIMS_ENTERPRISE_PAIRING_ENABLED=true` + `SHIMS_ENTERPRISE_URL`.
- **Chat tools** (read side): `enterprise.status`, `enterprise.query`
  (summary/dashboard/records/get/tables/search/overview/export/products),
  `enterprise.sync_brain`.
- **Agent tools** (gated writes): `enterprise.create` (typed creates + raw
  `records.insert`), `enterprise.update`, `enterprise.move`,
  `enterprise.delete`, `enterprise.ingest` (bulk messages/vendors/tally,
  document folders, corpus import).
- **Enterprise side**: `records.*` bridge command family in
  `shims_enterprise/bridge_control.py` — `records.tables|list|get|insert|
  update|delete|move`, schema-validated, audited, `users`/`audit_log` writes
  blocked.
- **Awareness**: a seeded scheduler job (`tool` action,
  `enterprise.sync_brain`, every `SHIMS_ENTERPRISE_SYNC_MIN` = 60 min) folds
  the live enterprise inventory into omni-brain knowledge
  (`source_type='enterprise'`).

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

**Config:** set `SHIMS_MUTATION_MODEL` to a native GGUF id (default: currently loaded native model).

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

**Config:** set `SHIMS_PLANNER_MODEL` to a native GGUF id for the planner model (default: currently loaded native model).

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

## UI Note — Command Center drawer

The right sidebar is the `#cmd-dock` drawer (opened from the topbar), with
collapsible sections: Agent Roster, Telemetry, Plans & Schedule, Units,
Enterprise Bridge (hidden when disabled), **Brain Controls** (reasoning
effort / temperature / context / parallel slots → `GET/POST
/api/settings/llm`, persisted via `_set_env_persistent`), Desktop Controls,
Event Feed. Thinking renders inline in chat; the old Thinking|Plans|Feed
tabbed sidebar no longer exists.

Specialist domains (`shared/agent_domains.py`) give the brain three modes:
inline scoped tool lenses (`SHIMS_TOOL_SCOPING=off` disables), background
specialist agents (`POST /api/agents/assign`, `agent.assign` tool,
`specialist_agent` task type surfaced in the Background Tasks list), and the
scheduled comms digest. Decluttered settings: one model picker (topbar),
advanced sections collapsed, dead UI removed.

- **CSS/JS**: `frontend/css/shims_omni.css`, `frontend/js/shims_omni.js`, `frontend/shims_omni.html`.

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

**Config:** set `SHIMS_NIGHTLY_MODEL` (highest precedence) or `SHIMS_IMPROVEMENT_MODEL` to a native GGUF id for the reflection model (default: currently loaded native model); `SHIMS_NIGHTLY_TIMEOUT_S` (default 1800) covers 100B-class GGUFs.

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

`shared/agent_loop.py` now has real `_openai_chat_raw` and `_google_chat_raw` transports, plus a generic `_openai_compatible_chat_raw` / `_openai_compatible_chat_stream` for Kimi, DeepSeek, and Qwen. The fallback chain native (when healthy) → Anthropic → OpenAI → Google works end-to-end (Kimi/DeepSeek/Qwen via the OpenAI-compatible transport; Ollama only when explicitly requested), and the LLM gateway routes these providers through the same transports.

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
| Factory | native | loaded native GGUF | local |

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
