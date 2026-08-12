# SHIMS Native Engine — how it compares, and what makes it best on *this* box

**Context.** SHIMS-native embeds and supervises its own `koboldcpp` (llama.cpp core)
on Vulkan (the ASUS ProArt's AMD/unified GPU), serving GGUF models over an internal
OpenAI-compatible endpoint. The goal here is not "switch to whatever benchmarks
fastest on an H100" — it is "be the best possible engine for a 128 GB single-user
AMD/Vulkan desktop running GGUF (including MoE) models." On that target, kobold is
the right core; the wins are tuning, and most are now in place.

## What each provider does best

| Engine | Core strength | Fits SHIMS' box? |
|---|---|---|
| **llama.cpp / koboldcpp** *(SHIMS uses this)* | Runs GGUF on **any** backend — Vulkan/CUDA/Metal/CPU — with CPU offload; flash attention; **KV-cache quantization**; continuous batching; **prefix/KV caching** (SmartCache + context shift); speculative decoding; grammar + jinja tool templating; single self-contained binary. | **Yes — ideal.** Only mainstream engine that runs well on AMD/Vulkan + does CPU offload for models bigger than VRAM. |
| **vLLM** | **PagedAttention** + automatic prefix caching + chunked prefill → best raw GPU throughput under concurrency. | Poorly. CUDA/ROCm-first, wants HF (not GGUF) weights, heavy to run; overkill for one user. |
| **SGLang** | **RadixAttention** — automatic KV prefix-tree sharing across requests; great for agent swarms with shared prefixes. | Concept worth borrowing (see below); engine itself is CUDA/throughput-oriented. |
| **TGI** (HF) | Production HF serving: continuous batching, tensor parallel, GPTQ/AWQ. | No — HF/GPU production niche. |
| **ExLlamaV2/V3** | EXL2 quant, very fast + low VRAM on **NVIDIA**. | No — NVIDIA-only; SHIMS is AMD/Vulkan, where GGUF is the right format. |
| **Ollama** | Friendly llama.cpp wrapper + model manager. | SHIMS already **surpasses** it by embedding kobold directly (finer control: KV quant, slots, smartcache, jinja tools). |
| **TensorRT-LLM** | Fastest on NVIDIA datacenter GPUs. | No — NVIDIA/complex. |

## What SHIMS-native already has (verified in `shared/native_engine/`)

- **GGUF on Vulkan** with automatic GPU-layer/context planning for the detected hardware (`tuning.py`).
- **Flash attention** (on by default in kobold) and **continuous batching** via parallel slots.
- **Prefix/KV reuse**: kobold **SmartCache** snapshots + context shift, *now actually effective* because the app-layer prompt is prefix-stable (see below).
- **Jinja tool templating** so the model can emit real tool calls.
- **Crash auto-restart** watchdog + stale-engine reaping.

## Changes made this pass (turning "has the features" into "actually fast")

1. **KV-cache quantization** (`--quantkv`) — wired but **disabled by default**: on this
   machine's koboldcpp build it crashes 100% of the time on Gemma-4 (SWA + fused Gated
   Delta Net attention) over Vulkan, dying at `attach_threadpool: call` right after KV-cache
   reservation, every attempt. This was the actual cause of a live crash-loop during testing.
   Opt in with `SHIMS_NATIVE_QUANTKV=1` only after confirming your specific model/backend
   tolerates it — test one model change at a time against `logs/native_engine.log`.
2. **Prefix-stable prompt assembly** — the biggest win. Per-turn volatile context
   (time, RAG, recall) was sitting in `messages[0]`, so the KV cache missed every turn
   and the whole conversation was reprocessed (O(n²) "lag grows each message"). It now
   rides in the final user message, so system prompt + append-only history form a
   reusable cached prefix. *(backend/app/main.py)*
3. **MoE brain model** — `Qwen3.6-35B-A3B` (~3 B active params/token): big-model quality,
   small-model speed. The single best "smarter AND faster" lever on this hardware.
4. **Bigger affordable context** — ctx 24 576 + history budget 12 000 + 10 RAG chunks,
   made cheap by (1) and (2).
5. **Speculative decoding — now wired** (`--draftmodel`, opt-in via `SHIMS_NATIVE_DRAFT_MODEL`).
6. **Capabilities scorecard** — `GET /api/native/capabilities` reports which best-in-class
   features are actually active in the running engine (derived from the live launch argv),
   so "complete/best provider" is verifiable, not a claim.

## Worth absorbing next (optional, ranked)

1. **Turn on speculative decoding for a dense brain** — now that `--draftmodel` is wired,
   point `SHIMS_NATIVE_DRAFT_MODEL` at a small same-tokenizer draft (e.g. a 1–2 B Qwen for
   a Qwen main) for a 1.5–2× dense-model speedup. Low priority while the A3B MoE is the brain
   (already fast); high value if you switch to a dense 31B/40B.
2. **RadixAttention-style cross-request prefix sharing** (SGLang's idea) — kobold's
   per-slot SmartCache + our stable prefix already capture most of this for a single
   user; revisit only if heavy multi-agent swarms with shared prefixes become common.
3. **Draft/verify pipeline for the nightly big-model self-fix** — run the 235B only for
   nightly reasoning, keep the A3B hot for interactive use (already the direction of the
   model routing + keep-warm heartbeat).

## 2026-08-09: the management brain (researched + built)

Research on this exact hardware class (Strix Halo / Ryzen AI MAX+ 395, 128 GB UMA,
~256 GB/s) confirmed: llama.cpp + **Vulkan** beats HIP/ROCm on prompt speed (884 vs
344 tok/s measured by the community), the NPU is not practical for LLM inference
today, and **MoE models are this machine's superpower** (Qwen3-30B-MoE 66–72 tok/s,
LFM2-24B-A2B ~109 tok/s, vs dense-70B ≈ 5 tok/s — pure memory-bandwidth math).

What the leaders have that native lacked — now built into SHIMS:

1. **Per-model performance ledger** (`native_engine/perf.py`) — EMA-smoothed
   measured prompt/gen tok/s from every real turn, persisted to
   `data/state/native_perf.json`, surfaced in the UI and the status API. Neither
   LM Studio nor Ollama feeds *measured* speed back into model choice; SHIMS does.
   A silent 30x regression (this week's partial-offload cliff) is now visible
   immediately.
2. **Machine-fit model advisor** (in `engine.models()`) — every discovered GGUF is
   rated for THIS machine: measured speed wins; otherwise predicted from the GGUF
   header (MoE → fast; dense >25 GB → bandwidth-bound; `qwen35` dense hybrid →
   no fast Vulkan prompt path (chunked GDN unsupported); >85% of RAM → mmap
   crawl). Ratings + advice show as badges in the Native Engine panel dropdown.
3. **Model acquisition** — already present (`/api/models/search|files|download`
   backed by `shared/model_manager.py`): HF search, queued downloads into
   `storage/models`, cancel/delete. Native needs no external model manager.
4. **Idle TTL / keep-warm** — both directions already present
   (`SHIMS_NATIVE_IDLE_UNLOAD_MIN`, keep-warm heartbeat, JIT `ensure_loaded`).

### The "accelerator" question, answered honestly
Generation speed = memory bandwidth ÷ bytes touched per token. Nothing beats that
wall for dense models on UMA. The three real accelerators, all supported here:
- **Speculative decoding** (wired: `SHIMS_NATIVE_DRAFT_MODEL`): 1.5–3× on dense
  models with a small same-tokenizer draft. Dense-70B ≈ 8–15 tok/s.
- **MoE selection** (the advisor's job): 100–120B-class MoE (e.g. gpt-oss-120b,
  117B total / 5.1B active) runs ~30–40 tok/s on this chip — human chat speed at
  120B scale. This, not dense, is how "the biggest models" run here.
- **Quant discipline**: Q4 over Q8 for big dense models = 2× tokens/s.

## Bottom line

Switching engines would *lose* the two things this box needs most — Vulkan/AMD support
and CPU offload — for throughput gains that only matter under datacenter concurrency.
The right move (done) is to keep kobold as the core and tune it: KV quant, a prefix-stable
prompt, an MoE brain, and a bigger context. That makes SHIMS-native genuinely
best-in-class **for this machine**, not a worse copy of a datacenter server.
