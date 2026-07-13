"""Generate the SHIMS Project Master Notes PDF.

Run: python scripts/generate_project_master_notes.py
Output: generated/SHIMS_Project_Master_Notes.pdf
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    ListFlowable, ListItem, PageBreak
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated" / "SHIMS_Project_Master_Notes.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)


def build_pdf() -> Path:
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle", parent=styles["Heading1"], fontSize=24,
        textColor=colors.HexColor("#0B3D91"), spaceAfter=20, alignment=1,
    )
    h1 = ParagraphStyle(
        "CustomH1", parent=styles["Heading1"], fontSize=16,
        textColor=colors.HexColor("#0B3D91"), spaceAfter=10, spaceBefore=16,
    )
    h2 = ParagraphStyle(
        "CustomH2", parent=styles["Heading2"], fontSize=13,
        textColor=colors.HexColor("#1E5AA8"), spaceAfter=8, spaceBefore=12,
    )
    h3 = ParagraphStyle(
        "CustomH3", parent=styles["Heading3"], fontSize=11,
        textColor=colors.HexColor("#334155"), spaceAfter=6, spaceBefore=10,
    )
    body = ParagraphStyle(
        "CustomBody", parent=styles["BodyText"], fontSize=10,
        leading=14, spaceAfter=8,
    )
    bullet = ParagraphStyle(
        "CustomBullet", parent=styles["BodyText"], fontSize=10,
        leading=14, leftIndent=14, bulletIndent=6,
    )
    small = ParagraphStyle(
        "CustomSmall", parent=styles["BodyText"], fontSize=9,
        leading=12, textColor=colors.HexColor("#475569"),
    )
    caption = ParagraphStyle(
        "Caption", parent=styles["Italic"], fontSize=9,
        textColor=colors.grey, alignment=1,
    )
    warn = ParagraphStyle(
        "Warn", parent=styles["BodyText"], fontSize=10,
        leading=14, textColor=colors.HexColor("#991B1B"),
    )

    story: list = []

    story.append(Paragraph("SHIMS Project Master Notes", title_style))
    story.append(Paragraph("Master index of files, security posture, and evolution roadmap", caption))
    story.append(Spacer(1, 0.4 * cm))

    meta = [
        ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["Version", "v16+ / 2026"],
        ["Scope", "Omni + Enterprise + Android + Shared"],
        ["Purpose", "Avoid re-analyzing the repo; track changes and roadmap"],
    ]
    story.append(make_table(meta))
    story.append(Spacer(1, 0.6 * cm))

    # SECTION 1: OVERVIEW
    story.append(Paragraph("1. Project Overview", h1))
    story.append(bullets([
        "SHIMS = Self-Hosted Intelligent Multi-agent System.",
        "Three products: Omni (personal AI), Enterprise (pharma/factory execution), Android mobile client.",
        "Backend: FastAPI + Python 3.11+; local SQLite default, PostgreSQL for plant deployment.",
        "Frontend: Vanilla JS PWA with Web Speech API; Android: Java/Kotlin + JNI + llama.cpp + MediaPipe.",
        "AI providers: Ollama, Anthropic Claude, OpenAI, Gemini, DeepSeek, Kimi, local transformers.",
        "Agent engine: Wave Engine v3 (parallel tools) + Neural Governor v1 (intent, routing, drift, arbitration).",
        "Safety model: Propose → Sandbox validate → Human approve → Apply → Archive.",
        "Default ports: Omni 8010, Enterprise 8020/8021, Desktop Bridge 8765.",
    ], bullet))

    # SECTION 2: DIRECTORY MAP
    story.append(Paragraph("2. Top-Level Directory Map", h1))
    story.append(two_col_table([
        ["shared/", "Core engine: AI, agent loop, tools, memory, scheduler, documents, chemistry, neural governor, self-evolution."],
        ["backend/", "Omni FastAPI backend (~7,900 lines). Chat, voice, media, brain, memory, coder, scheduler, skills, enterprise bridge."],
        ["shims_enterprise/", "Enterprise FastAPI backend + Jinja2 portal (~6,300 lines). Departments, R&D, QC/COA, warehouse, production, GMP."],
        ["frontend/", "Web UI: Omni HUD, Coder IDE, Neural Agent dashboard, PWA service worker."],
        ["android_app/", "Android Gradle project with on-device LLM, wake word, OCR, notifications."],
        ["shims_core/", "Legacy shared core (models, security, db, documents, sandbox)."],
        ["shims_omni/", "Legacy compatibility shim."],
        ["shims_personal/", "Legacy personal wake-word API shim."],
        ["apps/", "Launcher shims; generated app output."],
        ["desktop_bridge/", "WebSocket desktop bridge for Omni → local machine control."],
        ["termux_offline_runtime/", "Lightweight Termux/Android offline server fallback."],
        ["tests/", "Pytest suite (~60 files, versioned regression tests)."],
        ["scripts/", "Build helpers, setup, handoff PDF generators, smoke tests."],
        ["docs/", "Architecture, compliance, publishing, reference docs."],
        ["data/", "Runtime data: media, state, screenshots, voice profiles, web search, STT uploads."],
        ["storage/", "Persistent SQLite DBs, backups, agent edits, skills, downloads, corpus."],
        ["alembic/", "Database migration scripts."],
        ["logs/", "Runtime stdout/stderr logs."],
        ["release_checks/", "Validation report text files."],
    ]))

    story.append(PageBreak())

    # SECTION 3: KEY FILE NOTES
    story.append(Paragraph("3. Key File Notes", h1))
    story.append(Paragraph("Core agent & AI engine", h2))
    story.append(two_col_table([
        ["shared/agent_loop.py", "Agentic reasoning loop: plan → tool call → observe → continue. Drives wave execution."],
        ["shared/agent_wave.py", "Wave-based parallel tool execution; duplicate suppression, context builder."],
        ["shared/agent_tools.py", "Full agent tool registry (~80 tools): fs, shell, web, coder, memory, mail, enterprise, chem."],
        ["shared/ai.py", "Unified LLM provider abstraction: Ollama, OpenAI, Gemini, Anthropic."],
        ["shared/provider_registry.py", "Provider/model routing decisions."],
        ["shared/config.py", "Central settings object; used by all backends."],
        ["shared/autonomy.py", "Autonomy gating (L1–L3 levels)."],
    ]))
    story.append(Paragraph("Memory, brain, search, trust", h2))
    story.append(two_col_table([
        ["shared/omni_brain.py", "Long-term memory + RAG ingest/retrieval."],
        ["shared/memory_store.py", "Simple key/value memory store."],
        ["shared/search_query_planner.py", "Detects when chat should route to web search."],
        ["shared/web_crawler.py", "Page fetch + text extraction."],
        ["shared/trust_contract.py", "Evidence / trust scoring for answers."],
        ["shared/action_ledger.py", "Records/confirms external actions."],
    ]))
    story.append(Paragraph("Planning, scheduling, coder, self-evolution", h2))
    story.append(two_col_table([
        ["shared/desktop_planner.py", "Persistent multi-step plan storage + wave execution."],
        ["shared/plan_executor.py", "Runs plan steps through tools or agent loop."],
        ["shared/desktop_scheduler.py", "Cron-like task scheduler."],
        ["shared/coder_v3.py", "Full IDE backend (Monaco + xterm) with shell."],
        ["shared/self_evolver.py", "Guarded self-patch pipeline: propose, validate, approve, apply, archive."],
        ["shared/neural_agent.py", "Self-evolution dashboard backend."],
        ["shared/improvement_loop.py", "Evaluation-driven nightly improvement loop."],
    ]))
    story.append(Paragraph("Documents, OCR, vision, media", h2))
    story.append(two_col_table([
        ["shared/document_engine/branded_base.py", "Unified branded PDF base class for all SHIMS documents."],
        ["shared/ocr_service.py", "Offline image OCR."],
        ["shared/vision.py", "Image description via Anthropic/Ollama."],
        ["shared/media_tools.py", "Pollinations image / video generation."],
    ]))
    story.append(Paragraph("Enterprise / Pharma", h2))
    story.append(two_col_table([
        ["shared/enterprise_pharma_core.py", "Pharma R&D/QC/production data layer."],
        ["shared/enterprise_expansion.py", "QMS/LIMS/MES/eBR seed helpers."],
        ["shared/bmr_generator.py", "BMR PDF generator."],
        ["shared/bmr_validator.py", "GMP cross-check BMR vs corpus/SOPs."],
        ["shared/autonomous_engine.py", "Background auto-ingest/auto-BMR engine."],
        ["shared/ehs_engine.py", "EHS/carbon/effluent reports."],
        ["shared/regulatory_engine.py", "Regulatory document creation."],
    ]))
    story.append(Paragraph("Neural Governor", h2))
    story.append(two_col_table([
        ["shared/neural_governor/governor.py", "Main governance orchestrator."],
        ["shared/neural_governor/intent_classifier.py", "Intent classification."],
        ["shared/neural_governor/model_router.py", "Hardware-aware model selection."],
        ["shared/neural_governor/drift_detector.py", "6-signal output drift detection."],
        ["shared/neural_governor/circuit_breaker.py", "Auto-disable failing providers."],
        ["shared/neural_governor/lineage.py", "Response provenance / audit chain."],
    ]))
    story.append(Paragraph("Main apps", h2))
    story.append(two_col_table([
        ["backend/app/main.py", "Main Omni FastAPI app. Monolith with chat, voice, media, brain, memory, coder, scheduler, skills."],
        ["shims_enterprise/app.py", "Main Enterprise FastAPI app. Departments, dashboards, GMP, AI lab, copilot."],
        ["shims_enterprise/core.py", "Auth, roles, nav, page access, Jinja context."],
        ["frontend/js/shims_omni.js", "Core Omni frontend: chat stream, voice/STT/TTS, wake word, settings, tool cards."],
        ["frontend/shims_omni.html", "Main Omni chat HUD."],
        ["android_app/app/src/main/java/.../MainActivity.java", "WebView main activity; STT/TTS, model download, JS bridges."],
        ["desktop_bridge/bridge_server.py", "WebSocket server for desktop control."],
    ]))

    story.append(PageBreak())

    # SECTION 4: CONFIGURATION
    story.append(Paragraph("4. Critical Configuration & Env Vars", h1))
    story.append(two_col_table([
        ["SHIMS_SECRET_KEY", "JWT/session signing secret (default fallback is weak)."],
        ["SHIMS_BRIDGE_TOKEN / ENTERPRISE_BRIDGE_TOKEN", "Token for Omni ↔ Enterprise / desktop bridge (default fallback is weak)."],
        ["SHIMS_DEMO_MODE", "If true, seeds predictable demo passwords (default true)."],
        ["SHIMS_OMNIPOTENT_MODE", "If true, gates like self.patch auto-apply without approval."],
        ["SHIMS_ALLOW_SELF_EVOLUTION", "If true, bypasses explicit human approval for source patches."],
        ["SHIMS_ROUTER_MODEL", "Fast model for wave planning."],
        ["SHIMS_WAVE_ROUTER_SPLIT", "auto / always / never."],
        ["ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY", "Cloud provider keys."],
        ["OLLAMA_HOST", "Local model host (default 127.0.0.1:11434)."],
        ["SHIMS_OLLAMA_MODEL", "Default local model (default llama3.2:latest)."],
    ]))

    # SECTION 5: SECURITY FINDINGS
    story.append(Paragraph("5. Security Scan Summary", h1))
    story.append(Paragraph("No traditional malware was found. The following are legitimate-by-design powerful features that require hardening before production exposure.", body))
    story.append(two_col_table([
        ["CRITICAL — Default/demo credentials", "shared/database.py seeds admin123 in demo mode. scripts/reset_demo_passwords.py uses predictable passwords. Login page advertises admin/admin123."],
        ["CRITICAL — Weak fallback secrets", "SHIMS_SECRET_KEY, SHIMS_BRIDGE_TOKEN, ENTERPRISE_BRIDGE_TOKEN have weak defaults and only runtime warnings."],
        ["HIGH — Arbitrary shell execution", "shared/agent_tools.py _run_shell, shared/coder_v3.py run_shell_command, desktop_bridge/bridge_server.py _run_shell all run user/LLM input via shell."],
        ["HIGH — Self-modification bypass", "shared/self_evolver.py apply_guarded_change bypasses approval if SHIMS_ALLOW_SELF_EVOLUTION or omnipotent_mode is enabled."],
        ["HIGH — Unauthenticated powerful endpoints", "backend/app/main.py /agent/tool, /agent/run, /api/interpreter/run, /evolution/* appear to lack auth decorators or global middleware."],
        ["HIGH — Path traversal", "backend/app/main.py download_mobile_model uses replace-based sanitization bypassable by ....//. serve_screenshot lacks sanitization."],
        ["MEDIUM — Sandbox escape risk", "shared/skill_runtime.py and shared/code_interpreter.py use AST/blocklist sandboxes that can be bypassed with creative Python."],
        ["MEDIUM — CORS allow_origins=['*']", "backend/app/main.py allows any origin to call the API."],
        ["LOW — CDN supply chain", "Frontend loads JS/CSS from public CDNs without SRI hashes."],
        ["NOT FOUND", "No reverse shells, obfuscated base64/hex blobs, hardcoded cloud API keys in source, or unauthorized exfiltration endpoints."],
    ]))

    story.append(PageBreak())

    # SECTION 6: OMNI EVOLUTION
    story.append(Paragraph("6. SHIMS Omni — Full Evolution Path", h1))
    story.append(Paragraph("Goal: a self-modifying, self-improving, ever-evolving personal AI agent that exceeds Hermes-class latency and capability while keeping the user in control through one-tap approvals.", small))

    story.append(Paragraph("Phase 1 — Foundation & Safety (0–30 days)", h2))
    story.append(h3_text("Hardening"))
    story.append(bullets([
        "Replace all weak default secrets with cryptographically random values on first boot.",
        "Add startup blocker: refuse to run with demo credentials/secrets in non-localhost mode.",
        "Implement global auth middleware covering /agent/*, /api/interpreter/*, /evolution/*, /api/plans/run, /api/schedule.",
        "Fix path traversal in /mobile/download, /screenshot, wakeword endpoints using Path.resolve() and relative_to checks.",
        "Restrict CORS to configured origins; localhost-only default.",
    ], bullet))
    story.append(h3_text("Latency & Reliability"))
    story.append(bullets([
        "Pre-load default Ollama model on startup; surface model-ready indicator in UI.",
        "Fast mode switch: route synthesis to cloud when local model is cold, keep tools local.",
        "Per-wave telemetry cards showing plan, execute, and render latencies.",
        "Add request timeouts, retries, and circuit-breaker integration for all providers.",
    ], bullet))
    story.append(h3_text("Voice & UI Polish"))
    story.append(bullets([
        "Push-to-talk alternative to wake-word for noisy environments.",
        "Per-language voice preference with user-selectable TTS voices.",
        "Global stop button cancels stream, TTS, plan execution, and scheduled tasks.",
        "Command palette (Ctrl+Shift+P) for tabs, sessions, tools, and skills.",
    ], bullet))

    story.append(Paragraph("Phase 2 — Agentic Skill & Memory (30–90 days)", h2))
    story.append(h3_text("Plan Learning"))
    story.append(bullets([
        "Record tool_hint success/failure patterns per task description.",
        "Auto-generate a skill from any plan that succeeds twice.",
        "On plan failure, ask user for the correct step and update planner heuristics.",
        "Dependency-resolved plan execution with checkpoint and resume.",
    ], bullet))
    story.append(h3_text("Persistent Context"))
    story.append(bullets([
        "Auto-summarize long threads and store as episodic memory every N turns.",
        "Named projects: group sessions, files, plans, memories, and artifacts under a label.",
        "Prepend top-k relevant memories to system prompt on session start.",
        "Hybrid memory: keyword + vector + knowledge-graph edges between facts.",
    ], bullet))
    story.append(h3_text("Tool Mastery"))
    story.append(bullets([
        "Summarize tool results >4 KB before returning to model.",
        "Allow retry/edit of any tool call from chat history.",
        "Native multimodal reasoning over tool result cards (charts, images).",
        "Tool result verification: check file existence, command exit codes, URL reachability.",
    ], bullet))
    story.append(h3_text("Mail & Desktop Automation"))
    story.append(bullets([
        "Gmail OAuth setup wizard in settings panel.",
        "Rule engine: if sender/subject/body matches, label, archive, notify, or forward.",
        "Daily morning brief: unread count, priority senders, action items.",
        "Calendar-aware scheduling: avoid conflicts when creating reminders/tasks.",
    ], bullet))

    story.append(PageBreak())

    story.append(Paragraph("Phase 3 — Multimodal, Realtime & On-Device (90–180 days)", h2))
    story.append(h3_text("Vision & Media"))
    story.append(bullets([
        "Native vision reasoning over uploaded images and generated artifacts.",
        "Screen understanding via desktop bridge screenshots for contextual help.",
        "Video understanding with frame sampling and transcript extraction.",
        "Local image generation via diffusers/SDXL with prompt refinement loop.",
    ], bullet))
    story.append(h3_text("Realtime Voice"))
    story.append(bullets([
        "Half-duplex realtime pipeline: VAD → STT → LLM → TTS with interruption handling.",
        "Local openwakeword + faster-whisper + piper-tts stack as default.",
        "Emotion/intent detection in voice for adaptive responses.",
    ], bullet))
    story.append(h3_text("Android On-Device"))
    story.append(bullets([
        "Auto-select between MediaPipe, llama.cpp, and cloud based on task and battery.",
        "On-device RAG for personal documents and memories.",
        "Background sync of memories and skills when charging on Wi-Fi.",
        "Offline-first mode with queued actions and conflict resolution.",
    ], bullet))

    story.append(Paragraph("Phase 4 — Self-Improvement & Superseding Hermes (180+ days)", h2))
    story.append(h3_text("Guarded Self-Modification"))
    story.append(bullets([
        "One-tap approve/discard cards for every proposed code change with diff preview.",
        "Voice-enabled approval: 'yes, apply it' or 'no, discard'.",
        "Automatic sandbox validation: py_compile, pytest subset, smoke tests, rollback on failure.",
        "Immutable harness protection: self_evolver.py, security.py, config.py cannot be targeted.",
        "Change archive with full provenance: who approved, what tests passed, when applied.",
    ], bullet))
    story.append(h3_text("Continuous Learning Loop"))
    story.append(bullets([
        "Nightly improvement loop runs reliability evals, wave-latency benchmark, prompt A/B tests.",
        "Detect failure patterns and propose new skill, prompt variant, or self.patch.",
        "User thumbs up/down feed into a lightweight reward model affecting tool/prompt selection.",
        "Auto-generate regression tests from newly learned skills.",
    ], bullet))
    story.append(h3_text("Model Adaptation"))
    story.append(bullets([
        "LoRA/QLoRA fine-tuning pipeline on user conversation data for style and domain adaptation.",
        "Continual learning with replay buffer and elastic weight consolidation to avoid forgetting.",
        "Distill best cloud reasoning traces into smaller local model for offline use.",
        "Auto-evaluate local fine-tunes against held-out benchmark before promotion.",
    ], bullet))
    story.append(h3_text("Knowledge & RAG Gold Standard"))
    story.append(bullets([
        "Hybrid retrieval: dense + sparse + knowledge graph + web cache.",
        "Query expansion, hypothetical document embedding, and reranking.",
        "Agentic RAG: self-correct retrieval, verify facts against multiple sources, cite sources.",
        "Automatic knowledge graph construction from conversations and documents.",
    ], bullet))
    story.append(h3_text("Multi-Agent & Governance"))
    story.append(bullets([
        "Specialist agents (coder, researcher, planner, safety) coordinated by Neural Governor.",
        "6-signal drift detection (contradiction, hallucination, dependency, memory mismatch, role match, task completion).",
        "Arbitrator SLM corrects drifted outputs; lineage records full provenance.",
        "Constitutional AI layer: refuse, escalate, or rewrite requests against user-defined principles.",
    ], bullet))

    story.append(PageBreak())

    # SECTION 7: ENTERPRISE EVOLUTION
    story.append(Paragraph("7. SHIMS Enterprise — Full Evolution Path", h1))
    story.append(Paragraph("Goal: an autonomous pharma manufacturing operating system that makes smart, auditable decisions across production, QA/QC, warehouse, EHS, and regulatory affairs while keeping humans in the loop for high-risk actions.", small))

    story.append(Paragraph("Phase 1 — Plant-Ready Foundation (0–30 days)", h2))
    story.append(h3_text("Infrastructure & Security"))
    story.append(bullets([
        "Migrate SQLite to PostgreSQL; enable row-level security per docs/POSTGRES_RLS_SCAFFOLD.sql.",
        "Remove demo mode from production; rotate all seeded credentials.",
        "HTTPS reverse proxy with mutual-TLS option for plant integrations.",
        "Harden bridge token validation; IP allowlist for bridge commands; audit every command.",
        "Backup strategy: automated DB dumps, document corpus snapshots, configuration backups.",
    ], bullet))
    story.append(h3_text("GxP Compliance Baseline"))
    story.append(bullets([
        "21 CFR Part 11 / Annex 11 compliant e-signatures with user/pass + meaning.",
        "Audit trail for every create/update/delete on GxP records.",
        "Role-based access matrix with department + function separation.",
        "Electronic records integrity: checksums, versioning, immutable log.",
    ], bullet))
    story.append(h3_text("Core Modules"))
    story.append(bullets([
        "Material master with vendor qualification, specifications, and shelf-life tracking.",
        "Equipment master with calibration, maintenance, and qualification status.",
        "User training records linked to role permissions and SOP revisions.",
        "Document control: SOP, BMR, STP, specification versioning and approval workflow.",
    ], bullet))

    story.append(Paragraph("Phase 2 — GxP Depth & Quality Systems (30–90 days)", h2))
    story.append(h3_text("QMS — Quality Management System"))
    story.append(bullets([
        "CAPA: root cause, corrective action, preventive action, effectiveness checks.",
        "Change control: risk assessment, impact analysis, approval, implementation, closure.",
        "Deviation management: planned/unplanned deviation workflow with classification.",
        "Supplier/vendor management: qualification, audits, scorecards.",
        "Training management: curriculum, assessments, retraining triggers.",
    ], bullet))
    story.append(h3_text("LIMS — Laboratory Information Management"))
    story.append(bullets([
        "Sample registration, tracking, and chain-of-custody.",
        "Test method library, specification limits, out-of-specification (OOS) workflow.",
        "Instrument integration: auto-capture results from balances, HPLC, GC, UV.",
        "Stability study management with pull schedules and trending.",
    ], bullet))
    story.append(h3_text("MES/eBR — Manufacturing Execution"))
    story.append(bullets([
        "Electronic batch records with step-by-step operator guidance.",
        "Real-time equipment check, material weighment verification, e-signatures.",
        "In-process controls (IPC) with auto-capture and alerts.",
        "Yield and reconciliation calculations with automatic investigation triggers.",
    ], bullet))
    story.append(h3_text("DMS — Document Management"))
    story.append(bullets([
        "Controlled document lifecycle: draft, review, approve, train, obsolete.",
        "BMR/SOP corpus AI learning and semantic search.",
        "Automatic BMR validation against corpus and SOPs with findings report.",
        "Regulatory submission-ready document packages.",
    ], bullet))

    story.append(PageBreak())

    story.append(Paragraph("Phase 3 — Manufacturing Intelligence (90–180 days)", h2))
    story.append(h3_text("Production & Planning"))
    story.append(bullets([
        "Master production scheduling with equipment, labor, and material constraints.",
        "What-if scenario modeling: capacity, yield, cost, and lead-time impact.",
        "Auto-generated campaign plans based on demand and inventory.",
        "Shop-floor digital work instructions with AR overlay readiness.",
    ], bullet))
    story.append(h3_text("Warehouse & Supply Chain"))
    story.append(bullets([
        "WMS integration: receiving, put-away, picking, dispensing, shipping.",
        "FEFO/FIFO allocation with quarantine and hold status.",
        "Auto-reorder points and purchase request generation.",
        "Supplier delivery performance and inventory optimization.",
    ], bullet))
    story.append(h3_text("EHS & Sustainability"))
    story.append(bullets([
        "Effluent and emission monitoring with regulatory limit checks.",
        "Carbon footprint per batch and product.",
        "Occupational exposure band (OEB) handling and PPE guidance.",
        "Incident reporting, investigation, and CAPA linkage.",
    ], bullet))
    story.append(h3_text("Regulatory Affairs"))
    story.append(bullets([
        "DMF, ASMF, CEP filing event tracking and health authority correspondence.",
        "Regulatory submission document generation (IND, NDA, ANDA, MAA).",
        "Variation and changes management for marketed products.",
        "Pharmacovigilance intake and case processing workflows.",
    ], bullet))
    story.append(h3_text("AI/ML for Manufacturing"))
    story.append(bullets([
        "In-house ML models for yield prediction, impurity forecasting, equipment failure.",
        "Training datasets built from batch history, LIMS results, and environmental data.",
        "Model registry, validation, and deployment with GxP documentation.",
        "Explainable predictions for QA review and regulatory inspection.",
    ], bullet))

    story.append(PageBreak())

    story.append(Paragraph("Phase 4 — Autonomous Pharma Factory (180+ days)", h2))
    story.append(h3_text("Autonomous Decision Engine"))
    story.append(bullets([
        "Neural Governor routes all AI decisions through intent, memory, drift, and lineage checks.",
        "Autonomous engine continuously ingests documents, generates BMRs, validates, syncs memories.",
        "Smart batch release: auto-check QC, yield, deviation, equipment status, e-signatures.",
        "Predictive maintenance and auto-work-order generation for equipment.",
    ], bullet))
    story.append(h3_text("Cross-Domain Optimization"))
    story.append(bullets([
        "Product intelligence dashboard: cost, yield, impurity, route alternatives, market pricing.",
        "End-to-end orchestration: corpus → drug master → R&D → tech transfer → BMR → MES → release.",
        "Real-time manufacturing KPIs with automated alerts and root-cause suggestions.",
        "Supply-chain risk sensing: raw material shortages, vendor delays, regulatory changes.",
    ], bullet))
    story.append(h3_text("Human-in-the-Loop Governance"))
    story.append(bullets([
        "Risk-based approval matrix: low-risk actions auto-approved, high-risk escalated.",
        "One-tap approve/discard for AI-generated decisions in web and mobile apps.",
        "Full data lineage: every decision links to data, model, user feedback, and rationale.",
        "Override and rollback: authorized users can reverse AI decisions with full audit trail.",
    ], bullet))
    story.append(h3_text("Continuous Self-Improvement"))
    story.append(bullets([
        "Nightly retraining of enterprise ML models on latest batch and lab data.",
        "LLM fine-tuning on domain corpus for chemistry, regulatory, and manufacturing language.",
        "RAG knowledge base auto-updated from SOP changes, regulatory newsletters, and inspection reports.",
        "Cross-site memory federation for multi-plant learning while preserving data sovereignty.",
    ], bullet))

    story.append(PageBreak())

    # SECTION 8: SELF-IMPROVEMENT ARCHITECTURE
    story.append(Paragraph("8. SHIMS Self-Improvement Architecture", h1))
    story.append(Paragraph("The goal is to make both Omni and Enterprise continuously smarter through any combination of today's gold-standard techniques: LLM fine-tuning, RAG, agentic flows, eval-driven loops, ML, and symbolic validation.", small))

    story.append(Paragraph("8.1 Learning Modalities", h2))
    story.append(two_col_table([
        ["In-context learning", "System prompt includes relevant skills, memories, RAG chunks, and examples before generation."],
        ["RAG retrieval", "Dense + sparse + knowledge graph + web cache with reranking and query expansion."],
        ["Skill code learning", "New tools/functions written as executable skills and registered dynamically after AST review."],
        ["Prompt evolution", "A/B test system-prompt variants against eval cases; promote winners."],
        ["LLM fine-tuning", "LoRA/QLoRA on curated conversation/corpus datasets for style, tool use, and domain accuracy."],
        ["Reinforcement learning", "User thumbs up/down, completion success, and latency form a reward signal for router/arbitrator."],
        ["Continual ML", "Traditional ML models (yield, failure, impurity) retrained nightly with validated pipelines."],
        ["Self-evolution", "Proposed source patches validated in sandbox, approved by user, applied with automatic rollback."],
    ]))

    story.append(Paragraph("8.2 Improvement Flywheel", h2))
    story.append(bullets([
        "Observe: capture every turn, tool call, plan, success, failure, and user feedback.",
        "Evaluate: run nightly reliability, wave-latency, prompt, and GxP eval suites.",
        "Reflect: identify failure clusters, gaps, and root causes via telemetry and drift signals.",
        "Propose: generate skill, prompt variant, self.patch, or ML retraining job.",
        "Validate: sandbox tests, A/B benchmark, safety checks, regression tests.",
        "Approve: one-tap card (yes/no) or auto-apply for low-risk changes.",
        "Deploy: apply patch, register skill, promote prompt, deploy model.",
        "Monitor: track post-deployment metrics; rollback if degradation detected.",
    ], bullet))

    story.append(Paragraph("8.3 Easy Approval UX", h2))
    story.append(bullets([
        "Diff card with before/after syntax highlighting and risk level badge.",
        "One-click 'Yes, apply' / 'No, discard' buttons; keyboard shortcuts Y/N.",
        "Voice approval: 'yes, apply it' / 'no, discard'.",
        "Auto-apply whitelist: low-risk skills and prompt variants can bypass explicit approval.",
        "Undo button available for 5 minutes after any self-modification.",
    ], bullet))

    story.append(Paragraph("8.4 Gold-Standard Techniques Applied", h2))
    story.append(two_col_table([
        ["Agentic RAG", "Self-correcting retrieval with source verification and citation."],
        ["Chain/Tree-of-Thought", "Explicit reasoning traces for math, code, and multi-step decisions."],
        ["Reflection", "Model critiques its own draft and revises before final output."],
        ["Multi-agent orchestration", "Specialist agents coordinated by Neural Governor."],
        ["Constitutional AI", "User-defined principles constrain outputs and actions."],
        ["Function calling / tool use", "Native tool calling for capable models; fallback parsing for others."],
        ["DPO / RLHF", "Human feedback trains lightweight reward and preference models."],
        ["Model distillation", "Distill cloud reasoning into small local models for offline speed."],
        ["Vector + graph memory", "Semantic search plus structured relationship edges."],
        ["Continual learning", "Replay buffers and EWC to learn new tasks without catastrophic forgetting."],
    ]))

    story.append(PageBreak())

    # SECTION 9: SHARED EVOLUTION
    story.append(Paragraph("9. Shared / Cross-Cutting Evolution", h1))
    story.append(bullets([
        "Neural Governor: complete v1 rollout across Omni and Enterprise.",
        "Security: centralize auth middleware, secret rotation, path traversal fixes, sandbox hardening.",
        "Testing: reliability evals, wave-latency benchmark, prompt evolution tests, enterprise GxP tests.",
        "Documentation: keep AGENTS.md, architecture docs, and compliance docs in sync with code changes.",
        "Android: Google Play compliance, account deletion, abuse reporting, billing, offline-first mode.",
        "Deployment: docker-compose with healthchecks, Postgres option, Kubernetes manifest (future).",
        "Observability: structured logs, metrics endpoint, distributed tracing for agent loops.",
    ], bullet))

    # SECTION 10: MODIFICATION LOG
    story.append(Paragraph("10. Modification Log (Appendix)", h1))
    story.append(Paragraph("Append each change below. For editable logging, update generated/SHIMS_Project_Master_Notes_CHANGELOG.md and regenerate this PDF periodically.", body))
    story.append(Paragraph("Log format: Date | File(s) | Author | Description | Validation", h2))
    log_rows = _load_changelog_rows()
    story.append(two_col_table(log_rows if log_rows else [["(none)", "No changes logged yet."]]))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("(End of notes — add new rows to the CHANGELOG.md above this line)", caption))

    doc.build(story)
    return OUT


def make_table(rows: list[list[str]], widths=None) -> Table:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CustomBody", parent=styles["BodyText"], fontSize=10,
        leading=14, spaceAfter=8,
    )
    data = [[Paragraph(str(c), body if j == 1 else ParagraphStyle(
        "B", parent=body, fontName="Helvetica-Bold")) for j, c in enumerate(r)] for r in rows]
    t = Table(data, colWidths=widths or [4 * cm, 10 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0DDF0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def two_col_table(rows: list[list[str]]) -> Table:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CustomBody", parent=styles["BodyText"], fontSize=10,
        leading=14, spaceAfter=8,
    )
    data = [[Paragraph(str(c), body if j == 1 else ParagraphStyle(
        "B", parent=body, fontName="Helvetica-Bold")) for j, c in enumerate(r)] for r in rows]
    t = Table(data, colWidths=[5 * cm, 9 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def bullets(items: list[str], style) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(i, style)) for i in items],
        bulletType="bullet", leftIndent=14
    )


def h3_text(text: str) -> Paragraph:
    styles = getSampleStyleSheet()
    h3 = ParagraphStyle(
        "CustomH3", parent=styles["Heading3"], fontSize=11,
        textColor=colors.HexColor("#334155"), spaceAfter=6, spaceBefore=10,
    )
    return Paragraph(text, h3)


def _load_changelog_rows() -> list[list[str]]:
    """Parse the companion CHANGELOG.md into [date, details] rows."""
    changelog = ROOT / "generated" / "SHIMS_Project_Master_Notes_CHANGELOG.md"
    rows: list[list[str]] = []
    if not changelog.exists():
        return rows
    in_table = False
    for line in changelog.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "Date" not in stripped and "---" not in stripped:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 5:
                rows.append([cells[0], " | ".join(cells[1:])])
    return rows


if __name__ == "__main__":
    path = build_pdf()
    print(f"Generated: {path}")
