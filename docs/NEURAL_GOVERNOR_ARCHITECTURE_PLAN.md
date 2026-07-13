# SHIMS Omni Neural Governor v1.0 — Final Architecture Plan
## Gap-Analyzed, Model-Agnostic, Patent-Ready

**Date:** 2026-06-02
**Scope:** Cognitive governance layer for SHIMS Enterprise + Omni
**Philosophy:** Build the governor in Python first (safer, patent-ready). Latent steering (ENE-LWC) becomes v2 upgrade.

---

## 1. Executive Summary

The Neural Governor is a **local autonomous AI operating system** that sits above SHIMS' existing multi-provider AI layer. It does not replace Ollama, OpenAI, Gemini, or any other provider — it **governs them**.

For every AI request, the Governor:
1. Classifies intent and retrieves personal/enterprise memory
2. Routes to the optimal model (hardware-aware, cost-aware, permission-aware)
3. Monitors the draft output with **6-signal drift detection**
4. If drift exceeds threshold, invokes an **arbitrator + tool verification + memory reconciliation** pipeline
5. Delivers the final response with full **data lineage / audit chain**
6. Logs feedback and proposes **self-improvements** (sandboxed, benchmarked, admin-approved)
7. Continuously learns user style, factory patterns, and R&D habits

**Model agnostic:** Works with ANY provider in SHIMS — Ollama (any GGUF), OpenAI, Gemini, Claude, DeepSeek, Kimi, local transformers, Android MediaPipe, llama.cpp.

---

## 2. What the PDF Requires vs What SHIMS Has (Gap Matrix)

| Requirement (PDF) | SHIMS Status | Gap Severity | Resolution |
|---|---|---|---|
| Multi-signal drift detection (6 signals) | ❌ None | 🔴 Critical | New module: `neural_governor/drift_detector.py` |
| Arbitrator SLM | ⚠️ Ollama exists, no arbitrator role | 🟡 High | Repurpose small model as arbitrator |
| Local memory graph | ⚠️ `omni_brain` has keyword memory | 🟡 High | Add vector embedding + graph edges |
| RAG database | ⚠️ `knowledge_chunks` exists, no semantic search | 🟡 High | Add embedding-based retrieval |
| Web/tool verification | ✅ `agent_loop.py` + `agent_tools.py` | 🟢 Low | Wire into governor pipeline |
| Code/document/image/video modules | ✅ `document_engine/`, media gen, sandbox | 🟢 Low | Wire into governor pipeline |
| Safe self-evolution loop | ⚠️ `self_evolver.py` exists, no benchmark compare | 🟡 High | Add A/B benchmark + rollback + approval queue |
| Admin approval for risky changes | ⚠️ UI has approve/discard, no formal queue | 🟡 High | Add `governor_proposals` table + admin UI |
| Personal operating layer | ❌ None | 🔴 Critical | New module: `neural_governor/personal_layer.py` |
| Hardware-aware model selection | ❌ None | 🟡 High | Add `system_profiler.py` + model registry |
| Resource governance (CPU/GPU/ram) | ❌ None | 🟡 High | Add `resource_governor.py` |
| Adaptive failover (learned, not hardcoded) | ❌ Hardcoded fallback | 🟡 High | Add performance telemetry + routing table |
| Unified event bus | ❌ Bridge is HTTP req/res only | 🟡 High | Add `event_bus.py` (async pub/sub) |
| Data lineage / audit chain | ⚠️ `action_ledger.py` + `trust_contract.py` exist | 🟢 Low | Wire into governor decisions |
| Intent classifier | ❌ None | 🟡 High | Add lightweight classifier (rule + SLM) |
| Role/persona enforcement | ⚠️ Permissions exist, no AI persona match | 🟡 High | Add persona scoring to drift detector |
| RLHF / feedback loop | ❌ None | 🟡 High | Add thumbs up/down + weight update |
| Cross-system memory (Enterprise ↔ Omni) | ❌ Siloed | 🟡 High | Governor unifies context retrieval |
| Task priority scheduler | ⚠️ Background tasks exist, no QoS | 🟡 High | Add priority queue + preemption |
| Circuit breaker for failing providers | ❌ None | 🟡 High | Add failure tracking + auto-disable |
| Semantic chunking for documents | ❌ None | 🟡 High | Add chunking strategy to ingestion |
| Multi-modal governance (image/audio/video) | ❌ Text only | 🟡 High | Extend drift to multi-modal outputs |
| Differential privacy for learning | ❌ None | 🟢 Low | Future v1.1 |
| Patent documentation auto-gen | ❌ None | 🟡 High | Add `patent_writer.py` |

**Total gaps to fill: 24** — all planned below.

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE LAYER                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│  │ Enterprise  │ │  Omni HUD   │ │ Neural Gov  │ │  Admin Approval │  │
│  │   Portal    │ │             │ │ Dashboard   │ │     Queue       │  │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └─────────────────┘  │
└─────────┼───────────────┼───────────────┼──────────────────────────────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      NEURAL GOVERNOR v1.0 CORE                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     INTENT CLASSIFIER                            │   │
│  │   (Rule-based + SLM prompt: classify into task category)         │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              UNIFIED MEMORY & CONTEXT RETRIEVER                  │   │
│  │   ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────────┐  │   │
│  │   │  Personal   │ │ Enterprise  │ │   Omni      │ │  RAG     │  │   │
│  │   │   Layer     │ │    ERP      │ │  Brain      │ │  Vector  │  │   │
│  │   │  (SQLite)   │ │   (SQLite)  │ │  (SQLite)   │ │  (SQLite│  │   │
│  │   └─────────────┘ └─────────────┘ └─────────────┘ └──────────┘  │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              HARDWARE-AWARE MODEL ROUTER                         │   │
│  │   (Select optimal provider/model based on task, perf, cost, hw)  │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              BASE LLM DRAFT GENERATION                           │   │
│  │   (Any provider: Ollama, OpenAI, Gemini, Claude, etc.)           │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │           COGNITIVE GOVERNOR — 6-SIGNAL DRIFT DETECTOR           │   │
│  │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │   │
│  │   │Contradict│ │Hallucinat│ │ Tool-Dep │ │ Memory   │ │ Role │ │   │
│  │   │  ion     │ │ ion Risk │ │ endency  │ │ Mismatch │ │Match │ │   │
│  │   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────┘ │   │
│  │   ┌──────────┐                                                     │   │
│  │   │ Task-Comp│  Composite Drift Score [0.0 - 1.0]                  │   │
│  │   │ letion   │  Threshold: 0.38 (configurable)                      │   │
│  │   └──────────┘                                                     │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│         ┌──────────────────────────────────────┐                       │
│         │   DRIFT < THRESHOLD ?                │                       │
│         │   YES → Final Response               │                       │
│         │   NO  → Arbitrator Correction        │                       │
│         └──────────────────────────────────────┘                       │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              ARBITRATOR + TOOL VERIFICATION PIPELINE             │   │
│  │   ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────────┐  │   │
│  │   │  Arbitrator │ │    Tool     │ │   Memory    │ │  Web     │  │   │
│  │   │    SLM      │ │   Router    │ │  Reconcile  │ │ Verify   │  │   │
│  │   └─────────────┘ └─────────────┘ └─────────────┘ └──────────┘  │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              FINAL RESPONSE + DATA LINEAGE                       │   │
│  │   (Trust contract, action ledger hash, full provenance)          │   │
│  └─────────────────────────────┬───────────────────────────────────┘   │
│                                ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              FEEDBACK LOOP + SELF-EVOLUTION                      │   │
│  │   Thumbs up/down → Weight update → Patch proposal → Sandbox     │   │
│  │   → Benchmark A/B → Admin approval → Deploy / Rollback           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXISTING SHIMS LAYER                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│  │  Provider   │ │   Agent     │ │   Self      │ │   Document      │  │
│  │  Registry   │ │   Loop      │ │  Evolver    │ │   Engine        │  │
│  │  (ai.py)    │ │(agent_loop) │ │(self_evolver│ │(doc_engine)     │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────┘  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│  │ Omni Brain  │ │   Action    │ │    Trust    │ │    Sandbox      │  │
│  │(omni_brain) │ │   Ledger    │ │  Contract   │ │  (sandbox.py)   │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Specifications

### 4.1 Intent Classifier (`neural_governor/intent_classifier.py`)
**Purpose:** Route user requests to the correct task category.

**Approach:** Two-tier:
1. **Fast rule layer:** Regex/heuristic classification (code, document, image, question, command, data_ingest, bmr_query, equipment_query)
2. **SLM confirm layer:** Small model validates ambiguous cases

**Output:** `IntentCategory` enum + confidence score

```python
class IntentCategory(str, Enum):
    CONVERSATION = "conversation"
    CODE_GENERATION = "code_generation"
    DOCUMENT_FORMAT = "document_format"
    DOCUMENT_INGEST = "document_ingest"
    DATA_ANALYSIS = "data_analysis"
    MANUFACTURING_QUERY = "manufacturing_query"
    EQUIPMENT_QUERY = "equipment_query"
    QUALITY_CONTROL = "quality_control"
    MULTIMODAL = "multimodal"
    SYSTEM_COMMAND = "system_command"
    RESEARCH = "research"
    ADMIN = "admin"
```

### 4.2 Unified Memory & Context Retriever (`neural_governor/context_retriever.py`)
**Purpose:** Gather ALL relevant context before generation.

**Sources (in priority order):**
1. **Personal Layer** (`personal_layer` table) — user style, factory context, R&D habits
2. **Omni Brain** (`memories`, `episodes`) — conversation history, pinned facts
3. **Enterprise ERP** — active BMRs, equipment status, inventory levels, QC pending
4. **RAG Vector Store** — semantically similar document chunks
5. **Research Cache** — recent web searches, research items

**Key addition:** Vector embeddings for semantic retrieval.
- Use lightweight embedding model (e.g., `sentence-transformers/all-MiniLM-L6-v2` or Ollama `nomic-embed-text`)
- Store vectors in SQLite with `sqlite-vec` extension or simple numpy arrays
- Chunk documents with overlap (256 tokens, 50 token overlap)

### 4.3 Hardware-Aware Model Router (`neural_governor/model_router.py`)
**Purpose:** Select the best model/provider for each task.

**Inputs:**
- Intent category
- Required capabilities (code, reasoning, creativity, speed, multimodal)
- Hardware profile (RAM, VRAM, CPU cores, CUDA available, internet connectivity)
- User permissions (which providers allowed)
- Cost preference (local free vs cloud paid)
- Historical performance per provider per task type

**Outputs:** `RoutingDecision` with provider, model, fallback chain, estimated latency

**Hardware Profiler** (`neural_governor/hardware_profiler.py`):
```python
@dataclass
class HardwareProfile:
    total_ram_gb: float
    vram_gb: float
    cpu_cores: int
    cuda_available: bool
    cuda_version: str
    internet_available: bool
    battery_powered: bool  # for mobile
    disk_space_gb: float
```

**Model Registry** (`neural_governor/model_registry.py`):
Database of models with metadata:
- Name, provider, parameter count, quantization, VRAM required, RAM required
- Capabilities: text, code, reasoning, vision, audio, multimodal
- Speed rating, quality rating, cost per 1K tokens
- Offline capable: yes/no

### 4.4 Cognitive Governor — 6-Signal Drift Detector (`neural_governor/drift_detector.py`)
**Purpose:** Score draft output quality BEFORE sending to user.

**Six Signals:**

| Signal | Detection Method | Threshold |
|---|---|---|
| **Contradiction** | NLI model or SLM prompt: "Does output contradict context?" | > 0.5 |
| **Hallucination Risk** | RAG grounding check + confidence entropy | > 0.6 |
| **Tool Dependency** | Regex/pattern: does output suggest a tool should have been used? | > 0.5 |
| **User-Memory Mismatch** | Embedding similarity: output vs personal layer patterns | < 0.3 |
| **Role/Persona Mismatch** | Embedding similarity: output vs expected role tone | < 0.4 |
| **Task-Completion Confidence** | SLM self-eval: "Is the task fully answered? (0-1)" | < 0.5 |

**Composite Score:** Weighted average (weights configurable per role).

**Drift Report:** JSON object with per-signal scores, threshold comparisons, and recommended action.

### 4.5 Arbitrator Pipeline (`neural_governor/arbitrator.py`)
**Purpose:** Fix drifted outputs.

**When invoked:** Any signal exceeds threshold.

**Process:**
1. Feed original prompt + draft output + drift report into arbitrator SLM
2. Arbitrator generates corrected output
3. Run drift detector AGAIN on corrected output
4. If still drifting → escalate to tool verification
5. If tool verification needed → invoke agent loop with specific tools
6. If still failing → return "I need more information" with specific questions

**Arbitrator model:** Smallest capable local model (Gemma 2B, Qwen 0.5B, Phi-3 mini) — speed is critical.

### 4.6 Tool Verification Router (`neural_governor/tool_router.py`)
**Purpose:** Verify if external tools can resolve the request better.

**Integrated tools:**
- Web search (existing `shared/web_search.py`)
- Document analysis (existing `document_engine/`)
- Code execution (existing `sandbox.py`)
- Calculator / data analysis
- Enterprise ERP queries (BMR, equipment, inventory)
- Image generation (existing media pipeline)
- RAG retrieval (existing `knowledge_chunks`)

**Decision:** Router decides which tools to call, executes them, feeds results back to arbitrator.

### 4.7 Final Response & Data Lineage (`neural_governor/lineage.py`)
**Purpose:** Every response carries full provenance.

**Lineage Record:**
```python
@dataclass
class ResponseLineage:
    lineage_id: str  # UUID
    timestamp: datetime
    user_id: int
    request_text: str
    intent: IntentCategory
    routing_decision: RoutingDecision
    context_sources: List[str]  # which memory sources were used
    draft_output: str
    drift_report: DriftReport
    arbitrator_used: bool
    tools_used: List[str]
    final_output: str
    latency_ms: int
    trust_score: float
    action_ledger_hash: str  # links to immutable log
```

Stored in `governor_lineage` table. Displayed as "Trust Card" in UI.

### 4.8 Feedback Loop & RLHF (`neural_governor/feedback_loop.py`)
**Purpose:** Learn from user interactions.

**Feedback Types:**
- Thumbs up/down on response
- Edit/correction by user
- Regenerate request
- Implicit feedback (did user act on the output? did they follow the code?)

**Learning:**
- Update personal layer weights
- Adjust model router performance scores per task type
- Tune drift detector thresholds per user
- Store high-quality episodes as "exemplars" for few-shot prompting

### 4.9 Safe Self-Evolution Loop (`neural_governor/evolution.py`)
**Purpose:** Propose and test improvements to itself.

**Process:**
```
1. Pattern Detection → Identify recurring task from lineage + feedback
2. Patch Generation → Generate code/template/skill patch
3. Sandbox Test → Copy to storage/sandbox/, run tests
4. Benchmark A/B → Compare against baseline on historical tasks
5. Proposal Queue → Add to `governor_proposals` table
6. Admin Review → Owner/executive approves/rejects with notes
7. Deploy → Atomic swap into shared/generated_skills/
8. Monitor → Track performance for 24h, auto-rollback if degraded
```

**Safety:**
- Cannot modify `neural_governor/` core files (only skills/templates)
- All patches must pass existing test suite
- Rollback within 60 seconds if error rate increases
- Admin approval required for ALL patches

### 4.10 Personal Operating Layer (`neural_governor/personal_layer.py`)
**Purpose:** Learn and adapt to each user.

**Stored per user:**
```python
@dataclass
class PersonalProfile:
    user_id: int
    writing_style: str  # formal, casual, technical, legal
    preferred_formats: List[str]  # pdf, docx, markdown
    sentence_length: str  # short, medium, long
    technical_depth: int  # 1-5
    factory_context: Dict  # department, equipment they use, common BMRs
    rd_habits: List[str]  # common experiment types, preferred methods
    document_patterns: List[str]  # types of docs they generate
    workflow_sequences: List[Dict]  # common multi-step workflows
    active_projects: List[str]
    communication_tone: str
    correction_history: List[Dict]  # what they commonly correct
    peak_hours: List[int]  # when they typically work
```

**Learning triggers:**
- Document ingestion patterns
- AI Lab mode preferences
- Bridge command history
- BMR/MES interaction patterns
- Manual corrections to AI output
- Time-of-day patterns

### 4.11 Resource Governor (`neural_governor/resource_governor.py`)
**Purpose:** Prevent system overload.

**Monitors:**
- CPU usage per request
- RAM usage
- VRAM usage (nvidia-smi parsing)
- Disk I/O
- Network latency

**Actions:**
- Queue requests if load > threshold
- Downgrade model to smaller variant automatically
- Batch similar requests
- Evict old context to free memory
- Pause non-essential background tasks

### 4.12 Event Bus (`neural_governor/event_bus.py`)
**Purpose:** Unified pub/sub for cross-system communication.

**Topics:**
- `ai.request_started` / `ai.request_completed` / `ai.drift_detected`
- `memory.updated` / `memory.consolidated`
- `evolution.proposal_created` / `evolution.proposal_approved`
- `enterprise.bmr_updated` / `enterprise.equipment_status_changed`
- `user.presence_changed` / `user.preference_updated`

**Consumers:**
- Governor dashboard (real-time updates)
- Omni HUD (sync status)
- Enterprise portal (notification badges)
- Admin approval queue (push notifications)

### 4.13 Circuit Breaker (`neural_governor/circuit_breaker.py`)
**Purpose:** Auto-disable failing providers.

**Logic:**
- Track error rate per provider per 5-minute window
- If error rate > 50% for 3 consecutive windows → OPEN circuit
- During OPEN: route to fallback, periodically HALF-OPEN to test
- After 5 minutes of success → CLOSE circuit

### 4.14 Patent Documentation Generator (`neural_governor/patent_writer.py`)
**Purpose:** Auto-generate patent specs from running system.

**Outputs:**
- Architecture diagram (Mermaid / SVG)
- Claim language (numbered, nested)
- Technical specification with examples
- Form 1 and Form 2 templates for Indian Patent Office

---

## 5. Database Schema Additions

### 5.1 New Tables

```sql
-- Core governance
CREATE TABLE governor_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lineage_uuid TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    session_id TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    intent TEXT,
    provider TEXT,
    model TEXT,
    request_text TEXT,
    draft_output TEXT,
    drift_score REAL,
    drift_report_json TEXT,
    arbitrator_used INTEGER DEFAULT 0,
    tools_used_json TEXT,
    final_output TEXT,
    latency_ms INTEGER,
    trust_score REAL,
    action_ledger_hash TEXT,
    feedback_rating INTEGER,  -- null, 1, or -1
    feedback_notes TEXT
);

CREATE INDEX idx_lineage_user ON governor_lineage(user_id, timestamp);
CREATE INDEX idx_lineage_session ON governor_lineage(session_id);

-- Vector memory (semantic search)
CREATE TABLE vector_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,  -- 'knowledge_chunk', 'memory', 'episode', 'document'
    source_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,  -- numpy float32 array
    text_content TEXT,
    metadata_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_vector_source ON vector_embeddings(source_type, source_id);

-- Model performance telemetry
CREATE TABLE model_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL,
    success INTEGER DEFAULT 1,
    latency_ms INTEGER,
    tokens_input INTEGER,
    tokens_output INTEGER,
    drift_score REAL,
    user_feedback INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_telemetry_model ON model_telemetry(provider, model, task_type, timestamp);

-- Self-evolution proposals
CREATE TABLE governor_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_uuid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    patch_type TEXT,  -- 'skill', 'template', 'prompt', 'config'
    patch_content TEXT,
    affected_files_json TEXT,
    baseline_score REAL,
    sandbox_score REAL,
    improvement_delta REAL,
    test_results_json TEXT,
    status TEXT DEFAULT 'pending',  -- pending, approved, rejected, deployed, rolled_back
    proposed_by TEXT DEFAULT 'system',
    reviewed_by INTEGER,
    review_notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_at DATETIME,
    deployed_at DATETIME
);

-- Personal operating layer
CREATE TABLE personal_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    writing_style TEXT,
    preferred_formats_json TEXT,
    sentence_length TEXT,
    technical_depth INTEGER DEFAULT 3,
    factory_context_json TEXT,
    rd_habits_json TEXT,
    document_patterns_json TEXT,
    workflow_sequences_json TEXT,
    active_projects_json TEXT,
    communication_tone TEXT,
    correction_history_json TEXT,
    peak_hours_json TEXT,
    learning_enabled INTEGER DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Resource monitoring
CREATE TABLE resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    cpu_percent REAL,
    ram_used_gb REAL,
    ram_total_gb REAL,
    vram_used_gb REAL,
    vram_total_gb REAL,
    disk_used_gb REAL,
    disk_total_gb REAL,
    active_requests INTEGER,
    queue_depth INTEGER
);

-- Event bus log
CREATE TABLE event_bus_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    payload_json TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    consumed_by_json TEXT
);

CREATE INDEX idx_event_topic ON event_bus_log(topic, timestamp);
```

---

## 6. API Endpoints

### 6.1 Core Chat (Governor-Managed)
```
POST /api/neural-governor/chat
  → Full pipeline: intent → context → route → generate → drift-check → arbitrate → respond
  → Streaming SSE with lineage updates

GET /api/neural-governor/lineage/{lineage_id}
  → Full provenance for any response

POST /api/neural-governor/feedback
  → Submit thumbs up/down + notes for a lineage_id
```

### 6.2 Memory Management
```
POST /api/neural-governor/memory/search
  → Semantic search across all memory sources

POST /api/neural-governor/memory/add
  → Add explicit memory with embedding

DELETE /api/neural-governor/memory/{id}
  → Forget memory

POST /api/neural-governor/memory/consolidate
  → Trigger background consolidation
```

### 6.3 Model Router
```
GET /api/neural-governor/models
  → List all models with hardware compatibility scores

GET /api/neural-governor/models/recommended
  → Get recommended model for intent + hardware

POST /api/neural-governor/models/benchmark
  → Run A/B benchmark on two models for a task
```

### 6.4 Drift & Diagnostics
```
GET /api/neural-governor/drift/report/{lineage_id}
  → Per-signal drift scores

GET /api/neural-governor/drift/summary
  → Aggregate drift statistics per user/time

GET /api/neural-governor/diagnostics
  → System health: provider status, circuit breaker states, resource usage
```

### 6.5 Self-Evolution
```
GET /api/neural-governor/evolution/proposals
  → List pending/approved/rejected proposals

POST /api/neural-governor/evolution/proposals/{id}/approve
  → Admin approve

POST /api/neural-governor/evolution/proposals/{id}/reject
  → Admin reject with notes

POST /api/neural-governor/evolution/force-scan
  → Trigger pattern detection now
```

### 6.6 Personal Layer
```
GET /api/neural-governor/personal/profile
  → Get current user's personal profile

POST /api/neural-governor/personal/profile
  → Update explicit preferences

POST /api/neural-governor/personal/learn
  → Trigger learning cycle for current user

GET /api/neural-governor/personal/insights
  → Show what the system has learned about you
```

### 6.7 Event Bus
```
GET /api/neural-governor/events/stream
  → SSE stream of event bus (for real-time dashboard)
```

---

## 7. Frontend/UI Plan

### 7.1 Neural Governor Dashboard (`/neural-governor`)
**Template:** `shims_enterprise/templates/neural_governor_dashboard.html`

**Panels:**
1. **System Health Orb** — Real-time CPU/RAM/VRAM, active requests, queue depth
2. **Provider Status Grid** — Each provider with circuit breaker state, recent latency, error rate
3. **Drift Monitor** — Live drift score distribution, recent alerts
4. **Memory Graph Viz** — Force-directed graph of connected memories (D3.js or Cytoscape.js)
5. **Evolution Queue** — Proposals table with Approve/Reject buttons
6. **Personal Insights** — What the system has learned about the user
7. **Lineage Explorer** — Searchable table of all AI interactions with trust scores

### 7.2 Chat Interface (Governor-Powered)
**Integration:** Enhance existing AI chat endpoints to use governor pipeline.

**New UI elements:**
- **Trust Card** expandable below each response showing lineage
- **Thumbs up/down** buttons on every response
- **Drift Alert** banner when arbitrator corrected output
- **Model Badge** showing which model actually generated the response
- **Thinking Steps** expandable showing intent, context sources, tool calls

### 7.3 Admin Approval Queue (`/admin/evolution`)
**Template:** `shims_enterprise/templates/admin_evolution.html`

- List of pending proposals with diff viewer
- Benchmark comparison charts
- Approve/Reject with mandatory notes
- Rollback button for deployed proposals

### 7.4 Personal Profile Settings (`/settings/personal-ai`)
**Template:** `shims_enterprise/templates/settings_personal_ai.html`

- Writing style slider
- Technical depth selector
- Preferred output formats
- Factory context editor
- "What SHIMS knows about me" transparency panel
- Privacy controls: pause learning, delete personal data, export

---

## 8. Integration Points

### 8.1 Existing Endpoints to Governor-Enable
All these should OPTIONALLY route through the governor:
- `/api/ai/ask` → Governor chat
- `/api/bridge/command` → Governor arbitrates bridge actions
- `/api/ai-lab/run` → Governor selects mode + model
- `/api/ingest/folder` → Governor classifies + formats

**Implementation:** Add `?governed=true` query param. Default to false initially, toggle to true once stable.

### 8.2 Permission Integration
- New permission keys: `neural_governor_access`, `neural_governor_admin`, `neural_governor_evolution_approve`
- Owner can disable governor per role
- AI quota system extended to count governor-mediated tokens

### 8.3 Bridge Integration
- Omni can query governor status via bridge
- Enterprise can request Omni's governor for complex cross-system tasks
- Unified event bus keeps both in sync

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `shared/neural_governor/` package structure
- [ ] Implement `intent_classifier.py`
- [ ] Implement `hardware_profiler.py`
- [ ] Implement `model_registry.py` + `model_router.py`
- [ ] Implement `context_retriever.py` (keyword + basic embedding)
- [ ] Add database schema (all tables)
- [ ] Create `governor_lineage` logging
- [ ] API endpoints: `/api/neural-governor/chat`, `/diagnostics`

### Phase 2: Drift Detection & Arbitration (Week 2)
- [ ] Implement `drift_detector.py` with all 6 signals
- [ ] Implement `arbitrator.py`
- [ ] Implement `tool_router.py`
- [ ] Wire into existing `/api/ai/ask`
- [ ] Add drift report UI
- [ ] Add trust cards to chat

### Phase 3: Memory & Personal Layer (Week 3)
- [ ] Implement `vector_embeddings` with `sqlite-vec` or numpy
- [ ] Implement `personal_layer.py`
- [ ] Implement semantic search API
- [ ] Build memory graph visualization
- [ ] Build personal profile settings page
- [ ] Implement feedback loop (thumbs up/down)

### Phase 4: Self-Evolution (Week 4)
- [ ] Implement `evolution.py`
- [ ] Implement A/B benchmarking
- [ ] Build admin approval queue UI
- [ ] Implement auto-rollback
- [ ] Connect to `self_evolver.py` sandbox

### Phase 5: Resource Governance & Events (Week 5)
- [ ] Implement `resource_governor.py`
- [ ] Implement `event_bus.py`
- [ ] Implement `circuit_breaker.py`
- [ ] Build real-time dashboard
- [ ] Add provider status grid

### Phase 6: Patent Documentation & Polish (Week 6)
- [ ] Implement `patent_writer.py`
- [ ] Generate architecture diagrams
- [ ] Write `docs/NEURAL_GOVERNOR_PATENT_SPEC.md`
- [ ] Full test suite (pytest)
- [ ] Performance benchmarking
- [ ] Security audit

---

## 10. Testing Strategy

### 10.1 Unit Tests
- `test_governor_intent.py` — intent classification accuracy
- `test_governor_drift.py` — each of 6 signals independently
- `test_governor_router.py` — routing decisions under various hardware profiles
- `test_governor_personal.py` — personal layer learning accuracy
- `test_governor_evolution.py` — patch generation, sandbox, rollback

### 10.2 Integration Tests
- `test_governor_chat.py` — full pipeline end-to-end
- `test_governor_bridge.py` — bridge commands with governor enabled
- `test_governor_permissions.py` — role-based access
- `test_governor_events.py` — event bus pub/sub

### 10.3 Benchmarks
- `benchmarks/governor_latency.py` — measure overhead of governance layer
- `benchmarks/governor_quality.py` — compare output quality with/without governor
- `benchmarks/governor_drift_accuracy.py` — labeled dataset of good/bad outputs

---

## 11. Patent Claim Language (Draft)

**Independent Claim 1:**
> A local autonomous AI operating system comprising:
> - an intent classifier configured to categorize user requests into task types;
> - a unified memory retriever configured to fetch context from personal, enterprise, and knowledge graph sources;
> - a hardware-aware model router configured to dynamically select AI providers based on device capabilities, network status, and task requirements;
> - a multi-signal drift detector configured to score draft outputs for contradiction, hallucination risk, tool dependency, user-memory mismatch, role mismatch, and task completion confidence;
> - an arbitrator module configured to correct outputs exceeding a drift threshold;
> - a tool verification layer configured to invoke external verification when arbitrator correction is insufficient;
> - a data lineage tracker configured to record full provenance of each response;
> - a feedback loop configured to learn from user interactions and update a personal operating profile;
> - a safe self-evolution module configured to propose code patches, sandbox test them, benchmark against baseline, and require administrative approval before deployment;
> - wherein the system operates entirely on consumer hardware without requiring cloud connectivity.

**Key differentiation from ENE-LWC:**
- ENE-LWC does only cosine similarity + residual injection
- Neural Governor adds: intent classification, multi-signal detection, hardware-aware routing, personal layer, safe self-evolution, data lineage, tool verification, and operates model-agnostically without requiring hidden-state manipulation.

---

## 12. Model Agnosticism Guarantee

The Governor **never assumes a specific model architecture**. All interactions go through the existing `shared/ai.py` abstraction layer.

**Supported Providers (all existing + future):**
| Provider | Local/Cloud | Models | Governor Use |
|---|---|---|---|
| Ollama | Local | Any GGUF (Llama, Mistral, Gemma, Qwen, Phi, etc.) | Primary local path |
| Transformers | Local | Any HuggingFace model | Research/experimental |
| llama.cpp | Local | Any GGUF via server | Android/Termux path |
| MediaPipe | Local | On-device Gemma | Android flagship |
| OpenAI | Cloud | GPT-4o, o3, etc. | Fallback when local insufficient |
| Gemini | Cloud | 2.5 Flash/Pro | Fallback + multimodal |
| Claude | Cloud | Sonnet, Opus | Fallback for reasoning |
| DeepSeek | Cloud | V3, R1 | Code generation fallback |
| Kimi | Cloud | K1.5 | Long-context fallback |

**Hardware auto-selection:**
- 8+ GB VRAM → Route to local 7B-13B models
- 4-8 GB VRAM → Route to local 3B-7B models
- < 4 GB VRAM or mobile → Route to 1B-3B models or cloud
- No internet → Force local only, smallest viable model

---

## 13. Conclusion

This plan transforms the raw ENE-LWC concept into a **complete, patentable, production-grade AI governance system** that:
1. Leverages every existing SHIMS capability
2. Fills 24 identified gaps
3. Works with ANY model provider
4. Runs on consumer hardware (your Predator Helios 300)
5. Keeps YOU in control through admin approval queues
6. Learns your personal style and factory needs
7. Generates its own patent documentation

**Ready to build.**
