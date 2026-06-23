# Omni Builder — ready-to-run build prompts (Anthropic-key powered)

SHIMS Omni can now develop the codebase itself, paid by the **Anthropic key in `.env`**,
through the safety harness (sandbox compile-gate → backup → apply → git commit, with
automatic rollback if a file fails to compile). This is the engine for the Enterprise
deep-build **without spending Claude Code credits**.

## How to run a build

Start Omni (`start_omni.bat`), then POST each prompt to `/builder/run`. From PowerShell:

```powershell
$body = @{
  instruction = "....(paste instruction)...."
  targets     = @("shims_enterprise/routers/tech_transfer.py")   # files it may create/rewrite
  context     = @("shared/tt_scale.py","shims_enterprise/routers/rd.py")  # read-only grounding
  apply       = $true   # $false = preview only (generates + compile-checks, no write)
} | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8010/builder/run -Method Post -ContentType 'application/json' -Body $body |
  ConvertTo-Json -Depth 6
```

- **Always run with `apply=$false` first** to preview + confirm it compiles, then re-run with `apply=$true`.
- It commits per successful build; `git log --oneline` shows progress; `git checkout <file>` reverts.
- One build = one Anthropic call. Keep each prompt to one file (or a few small related files) for reliability.

> Tip: `targets` must be inside allowed roots (`shims_enterprise`, `backend`, `shared`,
> `frontend`, `tests`, `docs`, `scripts`). Never targets `shared/self_evolver.py`,
> `shared/security.py`, `shared/config.py` (immutable harness).

---

## Phase 1 — finish R&D brain-panel (surface the predictive engine)

**1a. Shared brain-panel widget**
- targets: `["shims_enterprise/static/shared/brain-panel.js"]`
- context: `["shims_enterprise/static/style.css"]`
- instruction: *Create a reusable vanilla-JS `brain-panel.js`: a collapsible right-side dock any page can mount with `BrainPanel.mount(elementId, {endpoint, title})`. It GETs the endpoint, renders sections for `flags` (severity badge, mechanism, mitigation), `predictions`, and `next_trial_designs`, with a Recompute button. Use the existing `var(--...)` design tokens from style.css. No framework, no build step.*

**1b. Mount it on the R&D experiment view**
- targets: `["shims_enterprise/templates/rd_experiment.html"]` (or the experiment view template — confirm name first via context)
- context: `["shims_enterprise/routers/rd.py","shims_enterprise/static/shared/brain-panel.js"]`
- instruction: *Mount brain-panel on the experiment view pointing at `/api/rd/v2/experiments/{id}/risks`; recompute on stage edit.*

## Phase 2 — finish Tech Transfer (engine `shared/tt_scale.py` already built)

**2a. Router + page**
- targets: `["shims_enterprise/routers/tech_transfer.py"]`
- context: `["shared/tt_scale.py","shared/tech_transfer.py","shims_enterprise/routers/rd.py"]`  *(create `shared/tech_transfer.py` data layer first if missing — see 2c)*
- instruction: *Create router `/api/tt/v2/...`: create TT project from a finalized R&D route + target batch size + vessel; GET scale analysis by calling `shared.tt_scale.scale_batch`. Follow the conventions in `routers/rd.py` (require_user_or_bridge, `{'ok': True,...}`, HTTPException(400)).*

**2b. Tests + workspace card**
- targets: `["tests/test_tt_scale.py"]`
- context: `["shared/tt_scale.py","tests/test_rd_predictive.py"]`
- instruction: *Write pure-function unit tests for `scale_batch` (no DB/LLM), mirroring `test_rd_predictive.py`: assert scaling factor, volume-fit flag on overfill, and risk flags fire on a large area/volume drop.*

**2c. Data layer (if needed)**
- targets: `["shared/tech_transfer.py"]`
- context: `["shared/rd_lab.py"]`
- instruction: *Idempotent schema for `tech_transfer_projects`, `tt_trials`, `tt_requirements` following the `ensure_*_schema()` + `db.ensure_columns` pattern in `shared/rd_lab.py`.*

## Phase 3 — Production readiness engine
- targets: `["shared/production_readiness.py","tests/test_production_readiness.py"]`
- context: `["shared/rd_predictive.py","shared/enterprise_pharma_core.py"]`
- instruction: *Pure deterministic `production_readiness.check(plan)` → RM ✓ / equipment ✓ / manpower ✓ / QC ✓ / documents ✓ with per-item blockers + a bottleneck view; pure-function tests with seeded fixtures.*

## BMR / COA self-ingestion (your priority)
- targets: `["shared/bmr_ingest.py","tests/test_bmr_ingest.py"]`
- context: `["shared/document_engine/regulatory_coa.py","shared/enterprise_pharma_core.py"]`
- instruction: *Create `bmr_ingest.py`: read PDFs/DOCX from a configurable folder (default the path in `SHIMS_BMR_CORPUS_DIR`), extract structure (product, stages, RM charges, process params, in-process checks, specs) into a normalized dict, and emit a reusable BMR template. Deterministic parsing first (pdfplumber/python-docx); optional LLM enrichment via `ask_ai`. Tests use a tiny synthetic doc, no network.*

> After ingestion exists, run a follow-up build to feed extracted BMR specs into the R&D
> route + COA template generation, matching JK Lifecare formatting (reuse the branded
> renderers in `shared/document_engine/` — `regulatory_coa.py`, `branded_canvas.py`).

## Document formatting (JK Lifecare house style)
When you drop your **SOP format** and **JK Lifecare BMR template** into `docs/`, run:
- targets: `["shared/document_engine/sop_engine.py"]`
- context: `["shared/document_engine/regulatory_coa.py","shared/document_engine/branded_canvas.py","<your SOP sample>"]`
- instruction: *Build an SOP renderer matching the supplied JK Lifecare SOP format (header/footer, numbering, section hierarchy, sign-off blocks) reusing `branded_canvas`. All document outputs must share this house style.*

---

## After each engine: make Omni smarter (brief §5)
Register each deterministic engine as an Omni skill so chat/voice can call it:
- targets: `["scripts/register_builder_skills.py"]`
- instruction: *Register `tt_scale.scale_batch`, `production_readiness.check`, `rd_predictive` as Omni skills via `shared/skills.save_skill(..., runtime='tool', tool_schema=...)` so the chat tool-router can invoke them.*
