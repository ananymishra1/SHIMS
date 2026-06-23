# Claude Code Execution Brief — SHIMS Enterprise deep-build (one-go)

**How to use this:** open this repo in Claude Code and paste:
> "Read `docs/CLAUDE_CODE_EXECUTION_BRIEF.md` and `docs/ENTERPRISE_REDESIGN_MASTER_PLAN.md`, then execute the whole brief in order. Do Phase 0 first and stop for my confirmation only if Phase 0 fails. Otherwise proceed through all phases, committing after each."

This brief makes Claude Code finish the original vision: a decluttered, feature-rich, AI-enabled
pharma-factory OS across **R&D, Tech Transfer, Production, QC, QA, Environmental, Regulatory,
Warehouse, Procurement, Sales/Accounts**, plus concrete **Shims Omni** improvements. The
information architecture is already redesigned and a first AI brain shipped (see §2); your job is
to build the remaining department depth on top of that foundation without regressing it.

> Shims Omni angle: Omni already pairs with Enterprise over the bridge and can read the workspace
> catalog (`GET /api/workspaces/catalog`). Where a feature needs an LLM, prefer routing through
> Shims' configured Anthropic key (`ANTHROPIC_API_KEY` / `SHIMS_FACTORY_MODEL`) **and** provide a
> deterministic local fallback so it runs offline. Each deterministic engine you build should also
> be exposed as an Omni skill (see §5) so both apps share one brain.

---

## 0. Non-negotiable guardrails (read before touching anything)

These come from real incidents in this codebase. Violating them has already destroyed a file.

1. **`git init` FIRST.** There is currently no version control. Before any edit:
   `git init && git add -A && git commit -m "baseline before deep-build"`. Commit after every
   department/phase. This single step turns any future mistake into a one-line `git checkout`.
2. **Never read-after-truncate.** Do NOT do `open(p,'w').write(open(p).read())` — the `'w'` blanks
   the file before the inner read runs. To rewrite, read into a variable first, then open `'w'`.
   This exact bug blanked `shims_enterprise/routers/rd.py`.
3. **Compile + test after every file.** `python -m py_compile <file>` then the relevant pytest.
   Never batch many edits without compiling. An empty file also "compiles", so also assert the
   file is non-empty / has the expected route count.
4. **Back up before large edits:** copy originals to `_backups/<date>/` (in addition to git).
5. **Immutable harness files — do not edit:** `shared/self_evolver.py`, `shared/security.py`,
   `shared/config.py`. Respect the self-modification safety model in `AGENTS.md`.
6. **Keep `backend/app/main.py` and `shims_enterprise/app.py` monoliths working** — prefer adding
   routers/modules over deep refactors (per `AGENTS.md`).
7. **No secrets in code or git.** `.env` stays untracked (add a `.gitignore`).
8. **Demo creds are demo only.** Don't rely on them for anything but local testing.

Environment: Windows, Python 3.11/3.12 venv at `.venv\Scripts\python`. Run tests with
`.venv\Scripts\python -m pytest -q`. Local models via Ollama; cloud via keys in `.env`.

---

## 1. Architecture & conventions you MUST follow

- **App entry:** `shims_enterprise/app.py` (FastAPI, ~7k lines, prefix-less page routes +
  `/api/...`), routers in `shims_enterprise/routers/<module>.py` included via `app.include_router`.
- **Shared engine:** `shared/` (data layer + brains). DB access via `shared/database.py` `db`
  (`db.one`, `db.query`, `db.execute`, `db.ensure_columns`). Schemas are created idempotently with
  `ensure_*_schema()` + `db.ensure_columns(table, {col: type})` — follow `shared/rd_lab.py` as the
  reference pattern for a department data layer.
- **Information architecture is registry-driven.** `shims_enterprise/workspaces.py` is the SINGLE
  source for navigation. To surface a feature, add a card there — do not hand-edit the sidebar.
  `core.nav_for()` and the hub page (`/workspace/<key>`, template `dept_hub.html`) render from it.
  Keep each role's visible sidebar short (Home + role hubs + shortcuts); depth lives in hubs + ⌘K.
- **Page rendering:** `core.render(request, template, ctx)` injects `user`, `nav`, `page_ctx`.
  New pages: add a route in `app.py` (or a router), a Jinja template extending `base.html`, and a
  workspace card. Gate access with `can_access_page` / `require_page_access` and `can_access`.
- **API conventions:** validation errors → `HTTPException(400, ...)`; auth → `require_user` (or
  `require_user_or_bridge` for Omni-callable endpoints). Return `{'ok': True, ...}`.
- **AI is no-LLM-first.** Every "brain" feature must (a) have a deterministic local path that works
  with no provider, and (b) call the LLM only as an explicit opt-in that degrades gracefully
  (`{'ai_available': False, 'message': 'AI offline. ...'}`). Use `shared/ai.py::ask_ai` (async,
  returns `AIResult` with `.text`; no `temperature` kwarg) or `shared/llm_gateway`. Reference the
  pattern in `shims_enterprise/routers/rd.py`.
- **Form rules (declutter):** product/material selector first; auto-fill from product master / BMR
  corpus / template; ≤4 required fields; inline validation; advanced fields behind a toggle.
- **Reuse the design system:** hub cards, `var(--...)` tokens, and widgets already in
  `shims_enterprise/static/style.css`. Build the shared widgets named in the master plan
  (`product-picker`, `rm-ledger`, `brain-panel`, `wizard-stepper`) once, reuse everywhere.
- **Tests:** put unit tests in `tests/test_<module>.py`. Prefer pure-function engines that test
  WITHOUT the DB/LLM (see `tests/test_rd_predictive.py`). Keep `tests/test_nav.py` green.

---

## 2. What is ALREADY done — do NOT rebuild, build ON it

- **IA redesign / declutter:** `shims_enterprise/workspaces.py` (registry), `core.nav_for()`
  rewritten to short role-based sidebar, `templates/dept_hub.html` + `templates/workspaces_index.html`,
  routes `/workspace` and `/workspace/{key}` in `app.py`, hub CSS in `static/style.css`.
- **Command palette + catalog:** `/api/search` also returns navigation destinations;
  `/api/workspaces/catalog` exposes the IA (bridge-readable); `EnterpriseClient.catalog()` in
  `shared/enterprise_client.py` lets a paired Omni navigate by intent.
- **R&D predictive-chemistry guardian (deterministic, offline):** `shared/rd_predictive.py`,
  endpoint `GET /api/rd/v2/experiments/{id}/risks`, tests `tests/test_rd_predictive.py` (7/7).
- **Master plan / per-department feature spec:** `docs/ENTERPRISE_REDESIGN_MASTER_PLAN.md` (§2 is
  your detailed requirements source for each department).
- **Open item to verify first (Phase 0):** `shims_enterprise/routers/rd.py` was reconstructed from
  bytecode after a truncation; confirm it compiles and the suite passes before building further.

Backups of the IA originals are in `_backups/redesign_20260617/`.

---

## 3. Phase plan (execute in order; commit after each)

### Phase 0 — Safety net & verification (do first)
- `git init`, add `.gitignore` (`.venv/`, `.env`, `__pycache__/`, `*.db`, `data/`, `logs/`,
  `storage/`, `.gradle-*`, `_backups/`), commit baseline.
- Run `.venv\Scripts\python -m py_compile shims_enterprise\routers\rd.py shims_enterprise\app.py
  shims_enterprise\core.py shims_enterprise\workspaces.py shared\rd_predictive.py`.
- Run `.venv\Scripts\python -m pytest tests/test_nav.py tests/test_rd_predictive.py -q`.
- Start the app (`start_enterprise.bat` or `scripts/start_shims.py --no-omni`), log in as each demo
  role, click every workspace hub and card; fix any 404/403/500. Commit "phase 0 green".

### Phase 1 — Shared widgets + R&D brain surfacing (finish R&D)
- Build reusable JS widgets (`static/shared/`): `product-picker.js`, `rm-ledger.js`,
  `brain-panel.js`, `wizard-stepper.js`. Brain-panel is a generic side dock that any page can mount.
- Surface the predictive guardian: add a brain-panel on the R&D experiment view that calls
  `/api/rd/v2/experiments/{id}/risks` and renders flags (severity, mechanism, mitigation),
  predictions, and next-trial designs. Add a live recompute on stage edit.
- Feed the deterministic flags into `shared/rd_brain.py` LLM calls as grounding context.
- Tests for any new pure logic. Commit.

### Phase 2 — Tech Transfer (scale-up math)
- Data layer `shared/tech_transfer.py` (idempotent schema): TT projects from a finalized R&D route,
  target batch size, vessel selection.
- **Deterministic engine** `shared/tt_scale.py`: charge/volume scaling, vessel-fit (working volume
  vs batch volume), heat-transfer area/volume ratio change → cooling/heating-time variance vs lab,
  mixing-time/tip-speed at scale, exotherm-at-scale flag, suggested hold points. Mirror the
  `rd_predictive` structure (flags + mitigations + indices). Pure-function unit tests.
- Router `routers/tech_transfer.py` (`/api/tt/v2/...`), page + template, workspace card under R&D.
- Pull equipment from the `equipment` tables; link TT project ↔ R&D experiment. Done-criteria in
  master plan §5. Commit.

### Phase 3 — Production readiness engine
- **Deterministic readiness check** `shared/production_readiness.py`: one call returns RM ✓ (from
  warehouse stock vs BMR demand), equipment ✓ (free/clean/qualified from `equipment`/`reservations`),
  manpower ✓, QC ✓, documents ✓ — with per-item blockers. Add bottleneck/occupancy view and a
  simple schedule conflict detector. Pure-function tests with seeded fixtures.
- Wire into `production_planning.html` as a readiness card + endpoint. Consolidate the existing
  production pages into the Production hub. Commit.

### Phase 4 — QC/QA source-of-truth merge
- Make LIMS the single sampling source; link QC samples ↔ R&D experiments ↔ plant batches.
- QA: SOP lifecycle, training + effectiveness, deviation→CAPA→change-control flow. Add a
  deterministic "audit-readiness score" and deviation classifier (rules first, LLM optional).
- Remove duplicate doc/sign-off flows per master plan §1.4. Commit.

### Phase 5 — Environmental (EHS) with real EC conditions
- Ingest the **JK Lifecare Environmental Clearance** conditions (search/extract from the EC; store
  as a config table `ehs_ec_conditions`). Use `docs/equipment_list_jk_lifecare.pdf` and any EC PDF
  in `docs/` as inputs; if the EC must be fetched online, do it via the allowed web tools.
- **Deterministic material-balance engine** `shared/ehs_balance.py`: per batch/product mass balance,
  gas emission → scrubber load, liquid effluent → CETP load, recovery-path evaluation (solvent
  recovery %, sellable-waste paths), and product-mix vs EC limits with guardrail flags. "Is this
  waste someone's RM?" matcher. Pure-function tests. Page + hub card. Commit.

### Phase 6 — Drug Regulatory (DMF backbone)
- Research DMF structure first (Type II API DMF: Open/Applicant's Part + Closed/Restricted Part;
  eCTD modules; WHO vs US FDA paths) and write `docs/DMF_REFERENCE.md`.
- `shared/regulatory_dmf.py`: DMF builder that assembles sections from R&D route + QC specs +
  pharmacopoeia references; gap-analysis vs a checklist; filing tracker. Page + hub card. Commit.

### Phase 7 — Warehouse waste/recovery + Procurement funnel
- Extend warehouse to one stock truth across RM → in-process → FG → recovered solvent → sellable
  waste → CETP. Add the **procurement-request intake + cross-check** so all department requests
  funnel through warehouse before a PO is raised (dedupe + stock cross-check). Reorder prediction +
  dead-stock + recovered-solvent reuse matcher (deterministic). Commit.

### Phase 8 — Sales/Marketing + Accounting (light)
- Orders → production feasibility signal; GST invoices/e-way bills (reuse existing `gst`/`erp`);
  receivables/payables; margin-by-product feeding the R&D cost-reduction goal. Hub + minimal screens.
  Commit.

---

## 4. Cross-cutting deliverables (apply in every phase)
- Each department gets: a clean hub (already auto-generated from the registry — add/adjust cards),
  ≤4-field create forms with auto-fill, a `brain-panel` mounting that department's deterministic
  engine, and `tests/test_<module>.py`.
- Add new pages to `_DEFAULT_PAGE_PERMISSIONS` (in `shared/enterprise_pharma_core.py`) for the roles
  that need them, and re-seed if necessary, so `can_access_page` allows them.
- Keep `tests/test_nav.py` passing (every nav URL must resolve; templated routes already handled).

## 5. Shims Omni improvements (do alongside, not after)
- For each deterministic engine (`rd_predictive`, `tt_scale`, `production_readiness`, `ehs_balance`),
  register an Omni skill (`shared/skills.py` / `skill.create_tool`, `runtime='tool'`) so Omni can
  call it by voice/chat. Keep the 5s sandbox + `ast` safety (per `AGENTS.md` Phase B).
- Teach Omni the Enterprise map: have the agent use `EnterpriseClient.catalog()` to answer
  "where do I do X" and deep-link the user into the right hub.
- Improve cold-Ollama UX: when no model is loaded, return a clear status from brain endpoints
  instead of a silent failure (already the pattern — apply everywhere).
- Wire the Enterprise ⌘K palette to reuse Omni's intent router for parity.

## 6. Final acceptance checklist (must all pass)
- [ ] `git log` shows a commit per phase; `.env`/`.venv`/db not tracked.
- [ ] `.venv\Scripts\python -m pytest -q` is green (including new `test_<module>.py`).
- [ ] `python -m py_compile` clean on every changed file; no empty files.
- [ ] Every role's sidebar ≤6 items; every hub card resolves; no duplicate pages.
- [ ] Each department has a deterministic engine + brain-panel that works with AI offline.
- [ ] Each engine is callable as an Omni skill; `/api/workspaces/catalog` reflects all new pages.
- [ ] App boots and all demo roles can complete their core workflow end to end.
- [ ] `docs/ENTERPRISE_REDESIGN_MASTER_PLAN.md` status tags updated to `[x]` as you finish.

## 7. Working style for Claude Code
- Use a TODO/plan, work phase by phase, commit frequently, run tests continuously.
- Prefer many small, compiled, tested edits over large risky rewrites.
- When extending the data layer, copy the idempotent-schema pattern from `shared/rd_lab.py`.
- When unsure of a downstream signature, grep the source — never guess (the AI endpoints in
  `rd.py` broke once from guessed `ask_ai`/`RDBrain` signatures; verify with `grep -n "def name"`).
- Stop and ask the human only if a phase's acceptance criteria cannot be met or data is missing
  (e.g., the EC document); otherwise keep going to the end.
