# SHIMS — Evolution Handoff (2026-08-05)

Path forward for the next work session and the nightly self-fix loop. Written after
a stability + native-engine hardening pass. Read top to bottom; the **Do first** and
**Roadmap** sections are the actionable parts.

---

## 1. What this pass changed (all test-verified)

| Area | Change | Where |
|---|---|---|
| Rogue emails | Direct send → **draft-only** chokepoint (`SHIMS_ALLOW_EMAIL_SEND=1` to override). Root cause: a stored Gmail token turned the test suite's `/mailbox/gmail/send` into real sends. Tests are now hermetic. | `shared/mailbox.py`, `tests/test_launch_hardening.py` |
| Model instability | Every agent lane follows the one Settings brain model (no mid-task model swaps, no dead-backend routing) | `shared/agent_model_router.py`, `.env` |
| Brain model | MoE. User set to **Qwen3-235B-A22B** (Q4, ~22B active) — smartest local model; ~140 GB so runs via mmap/paging (`SHIMS_NATIVE_TIMEOUT=3600`). A3B (Qwen3.6-35B) is the lighter one-line fallback. | `.env` |
| Crashes | Process manager no longer crash-loops the removed `shims_enterprise` / wrong-path Ollama | `scripts/shims_process_manager.py` |
| Cross-chat bleed | Global RAG excludes other sessions' conversation archives | `shared/omni_brain.py` |
| Exponential per-message lag | Volatile RAG/time moved out of `messages[0]` → KV prefix cache reuses across turns | `backend/app/main.py` |
| Context size | ctx 24 576, history budget 12 000, 10 RAG chunks (affordable via KV quant + prefix fix) | `.env`, `backend/app/main.py` |
| Native engine | KV-cache quantization (`--quantkv`), speculative decoding wiring (`--draftmodel`), capabilities scorecard | `shared/native_engine/*` |
| Desktop heartbeat | Always-on observe pulse + `GET /api/heartbeat` | `backend/app/main.py` |
| One-password bridges | `SHIMS_MASTER_PASSWORD` derives one bridge secret + gates UI/API (cookie session) | `shared/bridge_auth.py` |

Provider analysis: `docs/NATIVE_ENGINE_PROVIDER_COMPARISON.md`.

---

## 2. Do first (before anything else)

1. **Restart SHIMS.** Loads all `.env`/code changes and clears a live problem: **two
   `koboldcpp.exe` were running at once** (an orphan holding a second model in RAM).
2. **Relocate models off LM Studio** (with SHIMS stopped): `scripts/relocate_models.py`
   (dry-run), then `--apply`. Instant same-drive move; reversible.
3. **Optional password:** set `SHIMS_MASTER_PASSWORD=…` in `.env` to gate the UI and
   auto-provision every bridge from one secret.
4. **Set a strong `SHIMS_SECRET_KEY`** (currently weak/default — startup warns).
5. **Delete the stray draft** created to `x@example.com` during diagnosis (draft only,
   never sent).

---

## 3. Evolution roadmap (prioritized)

### P0 — finish the autonomy safety envelope
- **WhatsApp/channels: intentionally left as-is** — no sends originate there; not a risk surface.
- Extend the **draft/approve chokepoint** from email to the remaining *write* surfaces:
  **enterprise write commands** and any **purchase/payment** path. Mirror the
  `email_direct_send_allowed()` pattern — one gate per surface + an audit-ledger entry.
  *(The rogue-send class only ever came from email; this closes the remaining theoretical gaps.)*

### P1 — native engine → "complete provider"
- **Speculative decoding** for a dense brain (wired; set `SHIMS_NATIVE_DRAFT_MODEL`).
- **Quant-aware auto-tune:** make `tuning.compute_launch_plan` factor `SHIMS_NATIVE_QUANTKV`
  into the KV budget so it auto-allocates larger ctx (deferred this pass — the native
  tests pin exact ctx; update those fixtures alongside the change).
- **Grammar / JSON-schema constrained output** (kobold supports it) for reliable tool
  calls and structured extraction.
- **Native model lifecycle**: download/verify GGUFs into `storage/models` from within
  SHIMS so it never depends on an external model manager.

### P2 — heartbeat → real always-on manager
- Have the heartbeat **surface** insights to the dashboard feed (urgent comms, pending
  approvals, anomalies) as drafts, and feed a lightweight continuous-learning signal into
  the brain between the 30-min day report and the nightly self-fix.

### P3 — RAG quality
- Tune retrieval relevance (semantic thresholds, re-ranking) now that volume is cheap;
  measure end-to-end answer grounding.

### P4 — enterprise story
- `shims_enterprise` was removed ("omni only"). Decide: fold its factory/department
  features into `backend/app/main.py`, or rebuild. Until then, disable enterprise pairing
  (`SHIMS_ENTERPRISE_PAIRING_ENABLED=false`) — it points at a dead `:8020`.

---

## 4. Watch-items / known issues

- **Multiple repo copies detected** — `C:\d\SHIMS` (canonical), plus references to
  `C:\Users\direc\OneDrive\Desktop\SHIMS` (the active `.venv`) and `D:\SHIMS`. Confirm one
  canonical checkout + venv to avoid editing one copy and running another.
- **Test isolation**: the suite shares the real mailbox SQLite (a live Gmail token). Gmail
  send tests are now hermetic; audit other tests that may touch real accounts/APIs.
- **Pre-existing test failure**: `test_regulatory_coa_renders_pdf` imports the removed
  `shared.enterprise_pharma_core` — skip/xfail or restore the module.
- **pytest temp-dir**: `WinError 5` on `pytest-of-direc`; run with
  `--basetemp` inside the repo (OneDrive/AV locks the default temp).
- **Weak secrets**: `SHIMS_SECRET_KEY`, `ENTERPRISE_BRIDGE_TOKEN` are default/weak.

---

## 5. For the nightly self-fix loop

Prioritize P0 (autonomy safety) and the P4 enterprise-pairing flag — both are small,
high-signal, and reduce risk. Verify every change with the affected `tests/` suite using
`--basetemp="<repo>/.pytest_tmp_run/bt"`. Never enable direct external actions
(`SHIMS_ALLOW_EMAIL_SEND`, autonomous sends) without an explicit human decision.
