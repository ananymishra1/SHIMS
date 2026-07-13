# v13 Self-Evolution Loop: Real Patch Application

The v13 loop is deliberately not an unrestricted self-modifying system. It is a guarded engineering workflow:

```text
telemetry + user feedback
  -> daily lessons
  -> patch proposal
  -> isolated sandbox copy
  -> tests/compile validation
  -> human approval phrase
  -> backup real file
  -> apply patch
  -> post-apply validation
  -> rollback on failure
  -> telemetry + archive
```

## Why this fixes the old bug

Earlier prototypes could show "Patch merged" in the Self-Evolution Lab while no real file changed. v13 removes that failure mode. The UI now calls backend endpoints that create proposal files, test them, and apply them to disk only after approval.

## API

```text
GET  /api/v13/evolution/proposals
POST /api/v13/evolution/propose
POST /api/v13/evolution/validate/{proposal_id}
POST /api/v13/evolution/apply/{proposal_id}
POST /api/v13/evolution/daily
```

The apply request requires:

```json
{"approved_by":"admin","approval_phrase":"I_APPROVE_SHIMS_PATCH"}
```

## Immutable safety harness

The self-evolver cannot modify:

```text
shared/self_evolver.py
shared/security.py
shared/config.py
.env
storage/
.venv/
```

This prevents the agent from weakening its own validation and approval controls.
