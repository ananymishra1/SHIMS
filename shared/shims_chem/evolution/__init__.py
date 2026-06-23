"""
Self-evolution: nightly improvement loop with a human-approval gate.

Three components, all behind feature flags:

  * archive.py   — Darwin-Gödel-style genome archive. Every candidate
    "improvement" (a system-prompt edit, a tool argument re-default, a
    LoRA adapter checkpoint) lives in a versioned SQLite archive with its
    eval scores. Lineage tracked. Rollback is one row.

  * harness.py   — deterministic eval harness. A fixed suite of (input,
    expected-verifier-verdict) cases drawn from episodic memory + a curated
    chemistry-test pack. Every candidate is run against this BEFORE it
    can be promoted.

  * loop.py      — orchestrates the nightly cycle: pull recent episodes,
    propose edits (prompt-level + LoRA-level), eval, write to archive,
    surface to a human approver. No edit is auto-applied; the gate is
    explicit.

  * qlora_train.py — Unsloth + QLoRA training script for the smart brain.
    Real, runnable when train deps installed.

CRITICAL: this loop never modifies the running brains in-place. All edits
go through the archive and require explicit promotion via the API.
"""
from .archive import Archive, Candidate, make_archive
from .harness import EvalCase, EvalReport, run_harness, default_cases
from .loop import propose_and_evaluate, promote_candidate, rollback_to

__all__ = [
    "Archive", "Candidate", "make_archive",
    "EvalCase", "EvalReport", "run_harness", "default_cases",
    "propose_and_evaluate", "promote_candidate", "rollback_to",
]
