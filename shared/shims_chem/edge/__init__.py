"""
Shims Edge — symbolic-teacher distillation for BitNet-class edge devices.

The idea, in one paragraph: instead of running a 14B model on every reactor
HMI, weighing balance, and labeler in the plant — which is impossible —
we distill the symbolic verifier's behavior into a tiny ternary-weight
network ({-1, 0, +1} per weight, BitNet b1.58 style) small enough to live
inside an industrial PC, a Raspberry-Pi-class node, or even legacy hardware.
Each edge node is a single-job specialist: one node verifies dispensed
masses against the BMR, another watches reactor thermals for incipient
runaway, another lints the COA before the printer fires. The fast brain
on the Helios orchestrates the swarm; the smart brain re-trains them
nightly off episodic memory.

What's in here:

  * distill.py     — symbolic-teacher distillation. We synthesize a
    dataset by calling our own verifier on millions of randomly perturbed
    inputs, then train a tiny ternary-weight network to imitate it.
    Symbolic teacher, BitNet student. Genuinely novel in pharma.

  * ternary.py     — pure-NumPy ternary inference. Loads a {-1, 0, +1} weight
    file produced by distill.py and runs it. Fast enough on a Pentium-class
    CPU (the EXO Labs result the user shared is the existence proof).

  * profiles.py    — device profiles: which jobs run where (weigh-balance
    micro-net, reactor-thermal micro-net, COA-lint micro-net, eBMR-helper
    micro-net), with target memory + latency budgets per profile.

  * deploy.py      — packaging: build a tarball + a manifest describing the
    target device, the entry-point, and the safety envelope. The pharma
    QA team approves the manifest; deployment is then deterministic.

  * runner.c.txt   — reference C runtime template (text file with notes so
    you can compile a ~50 kB native binary for the most constrained nodes).

This module is the genuinely new piece on top of the EXO Labs / BitNet
trick: nobody we've seen has applied symbolic-teacher distillation to
embed verifier-grade chemistry checks in actual factory-floor hardware.
"""
from .ternary import TernaryMLP, save_ternary, load_ternary
from .distill import build_distillation_dataset, distill_to_ternary, DistillConfig
from .profiles import EDGE_PROFILES, EdgeProfile
from .deploy import build_edge_bundle, EdgeManifest

__all__ = [
    "TernaryMLP", "save_ternary", "load_ternary",
    "build_distillation_dataset", "distill_to_ternary", "DistillConfig",
    "EDGE_PROFILES", "EdgeProfile",
    "build_edge_bundle", "EdgeManifest",
]
