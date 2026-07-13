"""
Edge device profiles — what runs where.

These are deliberately conservative. The 'pentium_ii_legacy' profile is the
proof-of-concept tier inspired by the EXO Labs result: a Pentium II / 128 MB
node from 1997 (we still find these in qualified label-printer and HMI
positions in API plants because the line is locked). The 'rpi_zero_2w'
tier represents the smallest practical modern node. 'imx8_industrial' covers
typical Siemens / ABB IPC hardware. Each tier has a memory and latency
budget the distilled micro-net must satisfy.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeProfile:
    profile_id: str
    cpu: str
    ram_mb: int
    typical_role: str
    max_model_kb: int            # hard ceiling on model file size
    max_latency_ms: int          # per-inference budget (single-thread, no GPU)
    floats_per_second: int       # very rough; for sanity-checking the inference budget


EDGE_PROFILES: list[EdgeProfile] = [
    EdgeProfile(
        profile_id="pentium_ii_legacy",
        cpu="Intel Pentium II 350 MHz (1997)",
        ram_mb=128,
        typical_role="Locked-down label printer / weighbridge controller (qualified hardware)",
        max_model_kb=200,
        max_latency_ms=2000,
        floats_per_second=10_000_000,
    ),
    EdgeProfile(
        profile_id="rpi_zero_2w",
        cpu="Raspberry Pi Zero 2 W (Cortex-A53 × 4 @ 1 GHz)",
        ram_mb=512,
        typical_role="Operator tablet helper / environmental monitoring node",
        max_model_kb=2000,
        max_latency_ms=400,
        floats_per_second=200_000_000,
    ),
    EdgeProfile(
        profile_id="imx8_industrial",
        cpu="NXP i.MX 8M (Cortex-A53 × 4 @ 1.5 GHz)",
        ram_mb=1024,
        typical_role="Reactor HMI / interlock controller",
        max_model_kb=8000,
        max_latency_ms=150,
        floats_per_second=500_000_000,
    ),
    EdgeProfile(
        profile_id="n100_kiosk",
        cpu="Intel N100 @ 3.4 GHz",
        ram_mb=4096,
        typical_role="Tablet / eBMR review terminal",
        max_model_kb=32_000,
        max_latency_ms=80,
        floats_per_second=2_000_000_000,
    ),
]


def get_profile(profile_id: str) -> EdgeProfile:
    for p in EDGE_PROFILES:
        if p.profile_id == profile_id:
            return p
    raise KeyError(f"Unknown profile: {profile_id}. Known: {[p.profile_id for p in EDGE_PROFILES]}")
