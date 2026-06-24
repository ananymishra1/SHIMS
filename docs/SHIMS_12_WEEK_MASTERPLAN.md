# SHIMS — Commercial 12-Week Masterplan & Status

This document consolidates the external (Manus) review + roadmap and reconciles
it against what **actually exists** in the SHIMS codebase, then tracks delivery.

**Legend:** ✅ done in this branch · 🟢 already existed (claim was inaccurate) ·
🟡 scaffolded (working code, needs infra/polish to be production-grade) ·
🔴 needs external infrastructure (payment processor, IdP, signing, CI/CD).

> Honesty note: a literal 12-week, multi-engineer program cannot be *finished*
> in one session. What is delivered here is the **working core of every
> workstream** as real, tested, dependency-free code — plus an exact map of the
> remaining infrastructure work. Nothing below is a mock; every ✅/🟡 item has
> code and (where logic exists) tests in the repo.

---

## Reconciling the external review

Several "missing"/"broken" claims were already solved in the live 83K-line repo
(the review analysed a subset). Corrections:

| External claim | Reality in repo | Status |
| --- | --- | --- |
| "No retry logic / budgets" | `agent_loop.py` has wave reflection + retry actions | 🟢 partial; wave-level retry added to plan |
| "Self-evolution can't ship in a packaged app" | `shims_core/self_evolution.py` already does propose→validate→apply→**backup/rollback** with path gating | 🟢 exists; formalized as Kernel/Cortex ✅ |
| "CORS/security weak" | `guardians.py` already restricts CORS + adds CSP/security headers + prod gating | 🟢 exists |
| "Agent loses memory on restart" | `_from_markdown` was a `pass` stub — **real bug** | ✅ fixed |
| "No onboarding" | `checkOnboarding()` was a no-op stub — **real gap** | ✅ fixed |
| "No skill editor / marketplace" | backend `/skills/save` existed; no marketplace, no editor UI | ✅ marketplace + editor added |
| "Per-user fine-tuning for behavior" | not present; fine-tuning is the wrong tool | ✅ lightweight ML engine added |

---

## Workstreams & 12-week schedule

### Phase 1 — Reliability & Memory (Weeks 1–2)
- ✅ **Scratchpad persistence bug fixed.** `shared/agent_scratchpad.py` now writes
  a lossless JSON sidecar and restores full plan/observations/notes on restart
  (markdown kept for humans). Test: `tests/test_growth_modules.py::TestScratchpadPersistence`.
- 🟢 Guarded self-evolution, CORS/CSP, prod validation already present.
- 🟡 **Wave-level retry/budget**: `agent_loop` has reflection-based retry; port the
  explicit `WaveBudget`/exponential-backoff from the refactor into `agent_wave.py`
  (low risk, additive) — *next concrete task*.

### Phase 2 — Onboarding & UI polish (Weeks 3–4)
- ✅ **Sales landing page** (`frontend/landing.html`) at `/`, app at `/app`.
- ✅ **First-run onboarding** flow (`frontend/js/shims_omni.js`): 4-step tour +
  sample prompts; `window.startOnboarding` to replay. Replaces the no-op stub.
- 🟡 Persistent chat-history UI & unified settings: sessions pane exists; add a
  searchable history rail and consolidate settings cards.

### Phase 3 — Behavior Learning (ML, not fine-tuning) (Weeks 5–6)
- ✅ **`shared/behavior_engine.py`** — four cheap CPU models (sequence/Markov,
  temporal hour·weekday, exponentially-decayed recency, 👍/👎 feedback) that emit
  confidence-scored predictions and a `to_context()` block injected into the LLM.
  Thresholds: 0.85 auto · 0.70 suggest · 0.50 rank. Persists to JSON.
  Tests: `TestBehaviorEngine` (6).
- ✅ API: `/behavior/suggestions`, `/behavior/record`, `/behavior/feedback`.
- 🟡 Wire `to_context()` into the agent system prompt and record real tool/action
  events from the chat loop (one call site in `agent_loop`/`main`).

### Phase 4 — Self-Evolution as Kernel/Cortex (Weeks 5–6, parallel)
- ✅ **`shared/cortex.py`** — formalizes Kernel (frozen: `shims_core/`, `backend/`,
  `guardians.py`, `config.py`) vs Cortex (hot-reloadable: skills, prompt overlay,
  tool defs). Code edits require human approval (delegates to the existing guarded
  engine with backup/rollback); reversible cortex content auto-applies above 0.85.
  Hot prompt overlay via `set_prompt_overlay`. Tests: `TestCortex` (5).
- ✅ API: `/cortex/status`, `/cortex/prompt-overlay`.

### Phase 5 — Skill Marketplace ("App Store for AI skills") (Weeks 7–8)
- ✅ **`shared/skill_marketplace.py`** — curated starter catalog, one-click
  install, portable export/import packs (`shims-skill-pack`). Tests: `TestMarketplace` (5).
- ✅ API: `/marketplace/skills`, `/marketplace/install`, `/marketplace/export`,
  `/marketplace/import`.
- ✅ **Skill editor UI** + marketplace browser in the Skills pane.
- 🔴 Hosted registry + publishing/revenue share (needs a server + auth).

### Phase 6 — Licensing & Monetization (Weeks 9–10)
- ✅ **`shared/licensing.py`** — offline, tamper-evident HMAC license keys; tiers
  Community/Pro/Enterprise with a feature→tier entitlement map; `is_entitled`,
  `current_entitlements`, `require()` upsell payloads. Tests: `TestLicensing` (6).
- ✅ API: `/license`, `/license/activate`.
- 🔴 Connect a billing backend (Stripe/Paddle) to *issue* keys; set
  `SHIMS_LICENSE_SECRET`. Add in-app upgrade modal driven by `require()` payloads.

### Phase 7 — Desktop Package (Weeks 3–4 / 11)
- ✅ **`apps/desktop/`** — Electron shell (`main.js`/`preload.js`/`splash.html`)
  that makes the existing custom titlebar work (`window.shimsDesktop`), auto-spawns
  the local backend, opens external links in the OS browser, and has
  `electron-builder` targets for win/mac/linux.
- 🔴 Code-signing certs + notarization + auto-update feed for distributable builds.

### Phase 8 — Enterprise (Weeks 11–12)
- 🟢 GMP/QA-QC/regulatory modules already exist (`shared/`), plus Postgres RLS SQL.
- ✅ Entitlement flags for `sso`, `audit_export`, `rls_multitenant`, `air_gapped_deploy`.
- 🔴 Real SSO (OIDC/SAML), team management UI, signed audit-log export.

### Phase 9 — Launch (Week 12)
- ✅ Pricing surface live on the landing page.
- 🔴 Product Hunt assets, docs site, demo video, telemetry opt-in dashboards.

---

## What shipped in this branch (files)

| File | Purpose |
| --- | --- |
| `shared/behavior_engine.py` | Local behavior-learning ML (4 models) |
| `shared/cortex.py` | Kernel/Cortex self-evolution orchestration |
| `shared/licensing.py` | Offline tiered licensing/entitlements |
| `shared/skill_marketplace.py` | Skill catalog / install / export-import |
| `backend/app/routes_growth.py` | API for all four (guarded include) |
| `shared/agent_scratchpad.py` | JSON round-trip persistence (bug fix) |
| `frontend/landing.html` | Sales/landing front door |
| `frontend/js/shims_omni.js` | Real onboarding + skill editor/marketplace UI |
| `apps/desktop/` | Electron desktop package |
| `tests/test_growth_modules.py` | 28 tests for the above |
| `tests/test_commercial_readiness.py` | landing + guardian hardening tests |

## Immediate next tasks (highest leverage)
1. Inject `behavior_engine.to_context()` + `cortex.get_prompt_overlay()` into the
   agent system prompt; record actions from the chat loop. *(1–2 call sites)*
2. Port `WaveBudget` + exponential backoff into `shared/agent_wave.py`.
3. Set `SHIMS_LICENSE_SECRET`, add the in-app upgrade modal.
4. Add code-signing + auto-update to `apps/desktop`.
