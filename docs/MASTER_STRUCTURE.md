# SHIMS Master Structure — Chat → Agent Loop → Tools → Skills

This document is the single map for how the SHIMS Omni chat pipeline, tool engine, and skill system fit together. Use it to decide where to make changes.

---

## 1. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                        │
│  frontend/shims_omni.html  +  frontend/js/shims_omni.js                      │
│  Calls: /brain/turn  (or WebSocket /converse/ws)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FASTAPI ROUTES                                       │
│  backend/app/main.py  (current home of all routes)                           │
│  - POST /api/chat           :6053  api_chat()                               │
│  - POST /chat/stream        :6435  chat_stream()                            │
│  - POST /chat/converse      :6439  chat_converse()                          │
│  - POST /brain/turn         :6431  brain_turn()                             │
│  - POST /agent/run          :7172  agent_run()                              │
│  - POST /agent/swarm        :7181  swarm endpoint                           │
│  - WebSocket /converse/ws   :6456  converse_ws()                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      STREAM GATEWAY                                          │
│  backend/app/main.py                                                         │
│  _safe_brain_stream(req)        :5668  — single entrypoint                  │
│  _brain_stream(req)             :4798  — full pipeline                      │
│  _brain_graph_stream(req)       :5742  — graph-agent path (flagged)         │
│  _fast_chat_stream(req)         :4602  — direct LLM fast lane               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AGENT LOOP                                           │
│  shared/agent_loop.py                                                        │
│  run_agent_loop()               :1380  — wave driver                        │
│  _router_chat() / _executor_chat()  :1565 / :1594                           │
│  plan_wave() / execute_wave()   in shared/agent_wave.py                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TOOL REGISTRY                                        │
│  shared/agent_tools.py                                                       │
│  TOOLS registry                 :2360                                       │
│  run_tool(name, args)           :3465                                       │
│  tool_specs(names)              :3452                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │ Built-in     │  │ Dynamic      │  │ Memory /     │
            │ tools        │  │ skill tools  │  │ plan tools   │
            │ (shell, fs,  │  │ (runtime=    │  │ (memory.*,   │
            │ web, code…)  │  │ "tool")      │  │ plan.*)      │
            └──────────────┘  └──────────────┘  └──────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SKILL SYSTEM                                         │
│  shared/skills.py         — CRUD + relevance retrieval                       │
│  shared/skill_runtime.py  — runtime dispatch + dynamic tool registration     │
│  storage/skills/*.json    — plain JSON sidecars                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Chat route layer

Public chat/agent-stream endpoints live in `backend/app/routes_chat.py` and are
mounted into the main FastAPI app. They differ mainly in how they set
`agent_mode` and `conversation_mode` on `ChatRequest`.

| Route | Method | Purpose | File |
|-------|--------|---------|------|
| `/api/chat` | POST | Non-streaming, backward-compatible JSON response. | `routes_chat.py` |
| `/chat/stream` | POST | Streaming NDJSON. | `routes_chat.py` |
| `/chat/converse` | POST | Forces `conversation_mode=True`. | `routes_chat.py` |
| `/brain/turn` | POST | Primary Omni endpoint; streaming NDJSON. | `routes_chat.py` |
| `/api/v11/chat/turn` | POST | Legacy v11 shape. | `routes_chat.py` |
| `/agent/run` | POST | Forces `agent_mode=True`. | `routes_chat.py` |
| `/agent/swarm` | POST | Multi-agent swarm orchestration. | `main.py` |
| `/converse/ws` | WebSocket | Bidirectional NDJSON over WS. | `routes_chat.py` |

Every route converges on `_safe_brain_stream(req)` in `backend/app/main.py`.

### Key model

`ChatRequest` — Pydantic model in `backend/app/main.py`:
- `message`, `session_id`, `provider`, `model`
- `agent_mode`, `conversation_mode`, `images`, `voice`, `system_prompt`, etc.

---

## 3. Message processing pipeline

### `_safe_brain_stream(req)` (:5668)

1. **Agent-mode gate** (:5672) — enables for slash commands or if `_agentic_intent(text)` matches.
2. **Fast lane** (:5677) — simple chat bypasses planning/RAG/agent loop via `_fast_chat_stream()`.
3. **Graph-agent path** (:5687) — if `SHIMS_GRAPH_AGENT=1`, uses `_brain_graph_stream()`.
4. **Legacy pipeline** — `_brain_stream(req)`.

### `_brain_stream(req)` (:4798)

| Step | What happens | Key function / line |
|------|--------------|---------------------|
| STT correction | Voice input cleaned up | :4805 |
| Approval router | Yes/no replies to pending actions | :4823 |
| Planning | Decides the turn route | `_make_plan(req)` :2651 |
| Action requests | Auto-executes safe actions, gates risky ones | :4959 |
| Conversation review | Summarizes long threads | :5021 |
| Memory / RAG | Retrieves context from omni-brain | `brain_prompt_addendum()` :5052 |
| Direct tool routes | web_search, media generation, etc. | :5113 |
| **Agent loop** | Wave-based tool reasoning | `agent_loop.run_agent_loop()` :5270 |
| Auto-plan trigger | Creates persistent desktop plan | `_should_auto_plan()` :5303 |
| Direct LLM fallback | Final answer generation | `_run_llm()` :3403 |

### `_make_plan(req)` (:2651)

- Duplicate / silence guard.
- Detects agentic intent (`_detect_tool_intent`).
- Builds search understanding (`_understand_search_turn`).
- Resolves provider/model (`_resolve_provider_model`).

---

## 4. Agent loop and wave engine

### `shared/agent_loop.py`

`run_agent_loop(...)` at `:1380` is an async generator.

Lifecycle inside one turn:
1. Build tool specs from `shared/agent_tools`.
2. Initialize `AgentScratchpad`, `ContextManager`, `AgentState`, `ReasoningStream`.
3. Inject learned skills via `shared.skill_runtime` (:1457).
4. Loop over waves (max `max(2, max_steps // 2)`):
   - `agent_wave.plan_wave()` — LLM emits parallel tool calls.
   - Emit `tool_call` / `thought` events.
   - `agent_wave.execute_wave()` — run calls in parallel.
   - Handle approval gates (`needs_approval`).
   - Emit `tool_result`, `job`, `patch_proposal`, `approval_request`.
5. Synthesize final answer if no natural `final`.
6. Yield reflection/thought events, end with `{"__final__": {...}}`.

### `shared/agent_wave.py`

| Function | Purpose | Line |
|----------|---------|------|
| `plan_wave()` | LLM plans the next wave of parallel tool calls | :149 |
| `execute_wave()` | Runs calls concurrently; skips duplicates; stops on approval | :207 |
| `_parse_wave()` | Parses JSON wave plan or native tool_calls | :90 |
| `build_wave_context()` | Formats results for next planning turn | :263 |

---

## 5. Tool system

### Definition

`shared/agent_tools.py` — `Tool` dataclass (:2336):

```python
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], dict[str, Any]]
    risk: Callable[[dict[str, Any]], str] = field(default=lambda args: "safe")
```

Register a tool:

```python
_register(Tool(
    name="my_tool",
    description="Does a thing",
    parameters={"type": "object", "properties": {...}, "required": [...]},
    run=_run_my_tool,
    risk=lambda args: "gated" if args.get("dangerous") else "safe",
))
```

### Execution

`run_tool(name, args, *, allow_gated, session_id)` at `:3465`:
1. Looks up `TOOLS[name]`.
2. Calls `tool.risk(args)`.
3. If gated and `allow_gated=False`, returns `{"needs_approval": True, ...}`.
4. In omnipotent mode, risks are downgraded to safe.
5. Returns `{"ok": bool, ...}`.

### Skill-related tools

| Tool | Handler | Registration | Purpose |
|------|---------|--------------|---------|
| `skill.learn` | `_run_skill_learn` :813 | :2809 | Save a text skill. |
| `skill.create_tool` | `_run_skill_create_tool` :824 | :2811 | Save a `runtime="tool"` skill. |
| `skill.execute` | `_run_skill_execute` :849 | :2813 | Run any skill by ID/name. |
| `skill.list` | `_run_skill_list` :867 | :2815 | Browse learned skills. |

---

## 6. Skill system

### Schema and storage

`shared/skills.py` — skills are JSON sidecars in `storage/skills/`.

Core fields (all stored in the JSON sidecar):
- `id`, `name`, `summary`, `body`
- `tags`, `pinned`, `source`, `weight`
- `uses`, `created_at`, `updated_at`
- `runtime` — `"text" | "tool" | "python" | "jinja"`
- `tool_schema`, `tool_code`, `tool_name` — for `runtime="tool"`

Key functions:

| Function | File | Line | Purpose |
|----------|------|------|---------|
| `save_skill()` | `shared/skills.py` | :44 | Create or update a skill. |
| `get_skill()` | `shared/skills.py` | :106 | Load by ID. |
| `list_skills()` | `shared/skills.py` | :110 | List/filter skills. |
| `relevant_skills()` | `shared/skills.py` | :143 | Token-overlap scoring for prompt injection. |
| `forget_skill()` | `shared/skills.py` | :119 | Delete a skill. |
| `extract_skill_candidates()` | `shared/skills.py` | :173 | Regex heuristic extraction. |

### Runtime dispatch

`shared/skill_runtime.py`:

| Function | Line | Purpose |
|----------|------|---------|
| `_is_safe_ast()` | :62 | Reject imports/classes/non-`run` functions. |
| `_sandbox_exec()` | :96 | Run Python skill in restricted namespace. |
| `_register_skill_tool()` | :147 | Register a `runtime="tool"` skill as agent tool. |
| `register_all_skill_tools()` | :172 | Scan `storage/skills/` and register all tool skills. |
| `execute_skill()` | :187 | Dispatch by runtime type. |
| `skill_prompt_block()` | :230 | Build prompt fragment injected into agent loop. |

### Skill lifecycle

```
Creation
  ├── user/agent says "remember I prefer X" ──► extract_skill_candidates()
  ├── agent calls skill.learn
  ├── agent calls skill.create_tool  ──► save_skill(runtime="tool")
  ├── plan_learning.py converts completed plans
  └── feedback distillation in backend/app/main.py
            │
            ▼
    storage/skills/<id>.json
            │
            ▼
    register_all_skill_tools() scans at import + each run_agent_loop()
            │
            ▼
    skill_prompt_block() injects relevant skills into system prompt
            │
            ▼
    LLM can call the skill as text context or as a dynamic tool
            │
            ▼
    execute_skill() / run_tool() executes it
```

---

## 7. Data-flow tables

### A chat message becomes a response

| Stage | Input | Output | File : function (line) |
|-------|-------|--------|------------------------|
| HTTP request | JSON body | `ChatRequest` | `backend/app/routes_chat.py` route handlers |
| Stream gateway | `ChatRequest` | NDJSON stream | `_safe_brain_stream()` :5668 |
| Plan decision | message + history | route plan | `_make_plan()` :2651 |
| Memory retrieval | message | context block | `brain_prompt_addendum()` :5052 |
| Skill injection | message | system prompt fragment | `skill_prompt_block()` :230 |
| Wave planning | messages + tool specs | list of tool calls | `plan_wave()` :149 |
| Wave execution | tool calls | results | `execute_wave()` :207 |
| Synthesis | tool results + history | final text | `_llm_chat()` :228 |
| Response | events | NDJSON lines | route handler / WebSocket |

### A skill becomes a callable tool

| Stage | Input | Output | File : function (line) |
|-------|-------|--------|------------------------|
| Create skill | name + code + schema | JSON sidecar | `save_skill()` :44 |
| Register tool | skill JSON | entry in `agent_tools.TOOLS` | `_register_skill_tool()` :147 |
| Discover | message | prompt text | `skill_prompt_block()` :230 |
| Plan | tool specs | tool call | `plan_wave()` :149 |
| Execute | args | result dict | `execute_skill()` :187 or `run_tool()` :3465 |

---

## 8. Where to modify

| Want to change this … | Go here |
|-----------------------|---------|
| Add a new agent tool | `shared/agent_tools.py` — write `_run_<name>()` and `_register(Tool(...))`. |
| Make a tool require approval | Add a `risk` callable returning `"gated"`. |
| Change chat routing or add a chat route | `backend/app/routes_chat.py` (mounted into `backend/app/main.py`). |
| Change how the agent loop plans waves | `shared/agent_loop.py` `run_agent_loop()` :1380 and `shared/agent_wave.py`. |
| Change skill storage / schema | `shared/skills.py`. |
| Add a new skill runtime type | `shared/skill_runtime.py` `RUNTIME_WHITELIST` and `execute_skill()`. |
| Change skill injection into prompts | `shared/skill_runtime.py` `skill_prompt_block()`. |
| Change memory/RAG retrieval | `shared/omni_brain.py`. |
| Change model provider routing | `shared/agent_loop.py` LLM backend functions and `backend/app/main.py` `_resolve_provider_model()`. |

---

## 9. Structural improvements completed

1. **Chat-route monolith**: public chat/agent-stream routes moved to `backend/app/routes_chat.py` and mounted into the main app.
2. **Stale skill tools mid-turn**: `run_agent_loop()` now re-registers skill tools and rebuilds tool specs/valid names before each wave.
3. **Dynamic tool schemas missing from prompts**: `skill_prompt_block()` now includes truncated JSON parameter schemas for tool skills, and `_register_skill_tool()` registers the skill's actual schema instead of a generic placeholder.
4. **Skill lineage**: `save_skill()` now accepts `previous_version_id` and `created_from`; callers (agent loop, plan learning, feedback distillation, marketplace, etc.) pass provenance.
5. **No single map document**: this file (`docs/MASTER_STRUCTURE.md`) is the map.

---

## 10. Key tests

| Test file | Covers |
|-----------|--------|
| `tests/test_skill_runtime.py` | Dynamic skill tool registration, execution, and prompt schemas. |
| `tests/test_skills.py` | Skill CRUD and lineage fields. |
| `tests/test_routes_chat.py` | Smoke tests for extracted chat routes. |
| `tests/test_desktop_cowork.py` | Skills roundtrip and endpoint shapes. |
| `tests/test_growth_modules.py` | Skill confidence gating and marketplace. |
| `tests/test_teams_sso_registry.py` | Registry skill install. |
| `tests/test_agent_loop.py` | Wave loop behavior. |
