# Shims Omni & Shims Enterprise — A Concrete Architecture and Feature Blueprint (May 2026)

## TL;DR
- **Shims Omni** should be built as a LangGraph-orchestrated, MCP-native agent platform on FastAPI, with Qdrant + LanceDB for memory, a Darwin-Gödel-style sandboxed self-improvement loop (proposes → containerised eval → human gate → merge), a cascading STT→LLM→TTS voice pipeline (Deepgram Nova-3 with 6.84 % median streaming WER and sub-300 ms latency, or local Whisper-Large-v3-Turbo + Cartesia/Kokoro), and an Android shell that uses LiteRT-LM + Gemma 3 1B/3n E2B on-device and Ollama/llama.cpp for self-hosted backends — full autonomy is feasible only for low-risk loops and must default to "AI recommends, human approves."
- **Shims Enterprise** must be implemented as an integrated ERP+MES+LIMS+QMS+DMS+RIM stack on a single PostgreSQL+event-bus backbone, with every GxP-relevant write protected by 21 CFR Part 11 / EU Annex 11-grade audit trails, ALCOA+ controls, electronic signatures, and validated computer systems (CSV per GAMP 5); India-side compliance must add CDSCO Revised Schedule M (in force for large manufacturers since 28 Jun 2024, MSMEs from 31 Dec 2025), GSP-mediated IRP e-Invoice + e-Way Bill APIs, and eCTD v4.0 publishing.
- **Full lights-out autonomy is legally impermissible** for batch release, deviation closure, OOS final disposition, change-control approval, regulatory submission sign-off, supplier qualification, and any electronic signature meaning "I approve" — these require a named Qualified Person / authorised human under 21 CFR 11.10(j), Annex 11 §14, and Schedule M PQS clauses. The autonomy toggle should therefore expose **five graduated levels** (L0 Shadow → L4 Lights-out) with hard-coded "never autonomous" gates around the GxP-critical decisions listed above.

---

## Key Findings

1. **Agent frameworks have converged on a small set of patterns.** Anthropic's *Building Effective Agents* and OpenAI's Agents-SDK both promote five composable workflow patterns (prompt chaining, routing, parallelisation, orchestrator-workers, evaluator-optimiser) layered on top of an "augmented LLM" with tools + retrieval + memory. Complex frameworks add abstraction overhead, so production teams should start with direct API calls and adopt LangGraph only when durable state and human-in-the-loop checkpoints are needed.
2. **MCP is now the de-facto wire protocol** for tools and data sources after OpenAI's March 2025 adoption and Anthropic's December 2025 donation of MCP to the Linux Foundation's Agentic AI Foundation. [Medium](https://gregrobison.medium.com/the-model-context-protocol-the-architecture-of-agentic-intelligence-cfc0e4613c1e) Designing Shims Omni around MCP servers (not bespoke tool adapters) future-proofs the integration layer.
3. **Self-improving agents are real but bounded.** The Darwin Gödel Machine (Zhang et al., arXiv 2505.22954, May 2025) demonstrates that LLM agents can edit their own code and empirically validate gains on SWE-bench [arXiv](https://arxiv.org/abs/2505.22954) [arXiv](https://arxiv.org/html/2505.22954v2) using per-agent Docker sandboxes and an archive of "stepping-stone" variants. [arXiv](https://arxiv.org/html/2602.05848v1) Outside coding benchmarks, recursive self-improvement remains experimental; for Shims Omni it should be scoped to prompts/skills/tools first, code second, and never to the safety harness itself.
4. **Local LLMs on Android in 2026 are practical for the 1B–4B class.** Google's own benchmarks on the litert-community Hugging Face cards show Gemma 3 1B (int4 QAT) hitting exactly **47 tok/s decode on CPU and 56 tok/s decode on GPU on a Galaxy S24 Ultra**, [huggingface](https://huggingface.co/litert-community/Gemma3-1B-IT) and **85 tok/s decode on the Snapdragon 8 Elite NPU** of a Galaxy S25 Ultra (626 MB RAM, 529 MB model file). [huggingface](https://huggingface.co/litert-community/Gemma3-1B-IT) Qwen2.5-1.5B at int8 hits ~34 tok/s on S25 Ultra CPU. [huggingface](https://huggingface.co/litert-community/Qwen2.5-1.5B-Instruct) Gemini Nano via AICore is shared across apps with zero APK weight cost; [Android Developers](https://developer.android.com/ai/gemini-nano) [Google](https://developers.google.com/ml-kit/genai) LiteRT-LM (Google's MediaPipe LLM Inference API successor) and MLC-LLM are embeddable inside an APK; only Ollama requires Termux.
5. **Pharma manufacturing software is unambiguously a six-system stack.** ERP (resources/finance) + MES (execution, eBR) + LIMS (lab) + QMS (deviation/CAPA/change control) + DMS (controlled documents) + RIM (regulatory submissions) must all share one identity, one audit-trail substrate, and one event bus; bolt-on integrations between best-of-breed silos are the leading source of data-integrity findings.
6. **The Indian regulatory clock is aggressive.** CDSCO's Revised Schedule M (Gazette G.S.R. 922(E), 5 Jan 2024) [Qvents](https://qvents.in/news/cdsco-dcgi-health-ministry-india-timeline-revised-schedule-m-implementation-extension-december-2025/) extended GMP requirements to PQS, QRM, lifecycle validation, computerised systems and PQR — large manufacturers had to comply by 28 Jun 2024; MSMEs that filed Form A by 11 Apr 2025 got an extension to 31 Dec 2025, [Qvents](https://qvents.in/news/cdsco-dcgi-health-ministry-india-timeline-revised-schedule-m-implementation-extension-december-2025/) and CDSCO is now inspecting non-extension firms immediately. [Vaayath](https://vaayath.com/cdsco-revised-schedule-m-inspections-2025/) For e-invoicing, MFA on the IRP portal is mandatory [IRIS IRP](https://einvoice6.gst.gov.in/content/api-integration/) and the 30-day reporting limit applies from 1 Apr 2025 for AATO ≥ ₹10 Cr. [IRIS IRP](https://einvoice6.gst.gov.in/content/api-integration/)
7. **AI in pharma manufacturing has moved from "inspect after manufacture" to predictive control,** with vibration/acoustic sensing for tablet-press tooling wear, [F7i](https://f7i.ai/blog/beyond-the-buzz-7-real-world-ai-predictive-maintenance-use-cases-in-pharma-for-2025) multivariate SPC on spectral data, and model-predictive control of granulation/coating [IntuitionLabs](https://intuitionlabs.ai/articles/ai-pharma-smart-factory-gmp-manufacturing) — all of which the FDA's 2025 draft AI guidance allows only with a documented credibility assessment and human-in-the-loop for critical processes.
8. **Voice-first UX in 2026 demands a sub-500 ms mouth-to-ear loop.** Best-of-breed building blocks are Deepgram Nova-3 (6.84 % median streaming WER on 2,703 production audio files, sub-300 ms streaming at $0.0077/min — "a 54.2 % improvement over the next-best alternative at 14.92 %" per Deepgram's Nova-3 launch blog) or self-hosted Whisper-Large-v3-Turbo + Silero VAD; LLM via Groq-served Llama or Gemini Flash for ~200 ms TTFT; ElevenLabs Scribe v2 / Cartesia Sonic-3 / Kokoro for streaming TTS. Speech-to-speech models (OpenAI gpt-realtime-1.5, Gemini 3.1 Flash Live, Amazon Nova 2 Sonic) collapse the pipeline but [Softcery](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture) cost ~10× more.

---

## Details

### PART A — Shims Omni

#### A1. Modern agentic architecture (2025-2026 patterns)

Adopt the **Anthropic / OpenAI consensus pattern set** as Omni's core vocabulary. From Anthropic's *Building Effective Agents* (Dec 2024, refreshed in the 2025 "Architecture Patterns and Implementation Frameworks" PDF): *"the most successful implementations weren't using complex frameworks or specialized libraries. Instead, they were building with simple, composable patterns."* The five patterns Omni should implement as first-class graph nodes:

- **Prompt chaining** (deterministic multi-step LLM calls).
- **Routing** (classifier sends the request to a specialised sub-agent).
- **Parallelisation** (sectioning + voting).
- **Orchestrator–workers** (dynamic decomposition; the "agent" pattern proper).
- **Evaluator–optimiser** (a second LLM critiques and the first revises — the production analogue of ReAct + reflection).

Architectural rule: **deterministic-tool-first**. Anthropic warns that "agents trade latency and cost for better task performance" and recommends starting with workflows and only escalating to LLM-driven tool selection when the path is genuinely unknowable. For Omni, this means each user goal first hits a router; only goals the router cannot classify fall into an open-ended ReAct loop bounded by a max-iteration stopping condition.

**Framework choice (Python/FastAPI native):** Use **LangGraph** as the runtime (v0.4 / 1.0 in 2026 ships durable state, checkpointing with time-travel, and built-in human-in-the-loop interrupts). Per Intuz's *"Top 5 AI Agent Frameworks 2026 | Tested in 100+ Production Deployments"*: *"Based on our 12-month uptime data across client deployments: LangGraph leads at 9/10 reliability (state checkpointing + explicit error handling), AutoGen scores 8/10, CrewAI scores 7/10."* [Intuz](https://www.intuz.com/blog/top-5-ai-agent-frameworks-2025) Avoid CrewAI as the primary runtime (no built-in checkpointing, coarse error handling) but use its role-based DSL as a *thin layer* for declaring sub-agents.

Wrap every external capability behind an **MCP server** rather than a bespoke LangChain tool: MCP is now the cross-framework standard — *"OpenAI's official adoption of MCP in March 2025... the deprecation of the Assistants API, scheduled for sunset in mid-2026, compelling the entire developer ecosystem to migrate toward MCP-based architecture"* [Medium](https://gregrobison.medium.com/the-model-context-protocol-the-architecture-of-agentic-intelligence-cfc0e4613c1e) (Greg Robison, *The Architecture of Agentic Intelligence*, Medium, 2025). For inter-agent communication beyond MCP, also implement the **A2A (Agent-to-Agent) protocol** that Google's ADK and OpenAgents now expose — this lets external agents (a customer's procurement agent, a regulator's audit-bot) interoperate with Omni's supervisor.

#### A2. Persistent memory and RAG

Use the **CoALA taxonomy** (working / episodic / semantic / procedural) that the field has converged on. Per the December 2025 arXiv survey *"Memory in the Age of AI Agents"* (arXiv:2512.13564) and Atlan's framework comparison: *"hybrid episodic and semantic systems outperform single-type systems, particularly when semantic memory has been pre-trained."* [Atlan](https://atlan.com/know/episodic-memory-ai-agents/)

Concrete stack for Omni (local-first):

- **Working memory:** in-process Python dict + LangGraph state.
- **Episodic memory:** **Letta** (formerly MemGPT) or **Mem0** for self-editing conversation memory with timestamps. Per Chhikara et al., *"Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory"* (arXiv:2504.19413, ECAI 2025): *"Mem0 attains a 91 % lower p95 latency and saves more than 90 % token cost"* [Mem0](https://mem0.ai/blog/long-term-memory-ai-agents) — concretely 1.44 s vs. 17.12 s at p95, and ~1.8 K tokens per conversation vs. 26 K for full-context, on the LOCOMO benchmark.
- **Semantic memory:** **Qdrant** (Docker, HNSW, hybrid sparse+dense, RBAC with OAuth2/OIDC, ~20–30 ms queries on tens of thousands of docs) [Medium](https://medium.com/@vinayak702010/lancedb-vs-qdrant-for-conversational-ai-vector-search-in-knowledge-bases-793ac51e0b81) as the production-grade default; **LanceDB** as the embedded fallback for the desktop/Android shell (smaller footprint, IVF_PQ, 40–60 ms queries, [Medium](https://medium.com/@vinayak702010/lancedb-vs-qdrant-for-conversational-ai-vector-search-in-knowledge-bases-793ac51e0b81) no separate process).
- **Procedural memory:** versioned skill/prompt library on disk (markdown + YAML, similar to Anthropic's "Agent Skills" format opened as a shared standard in March 2026).
- **Consolidation daemon:** background process every 50–200 episodes that summarises raw conversations into semantic facts and deduplicates, following Mem0's extract→consolidate→store→retrieve pipeline. Practitioners report *"naive summarization pipelines lose roughly 20 % of encoded facts"* [Atlan](https://atlan.com/know/episodic-memory-ai-agents/) (Atlan, citing letta-ai and mem0ai GitHub issues), so use multi-pass extraction with deduplication.
- **RAG patterns:** hybrid BM25 + dense retrieval (Qdrant supports both natively), re-rank with a cross-encoder (bge-reranker-v2), and apply Anthropic-style **contextual retrieval** (prepend chunk-level context summaries before embedding). Per Anthropic's September 2024 *Introducing Contextual Retrieval* post, the full stack — contextual embeddings + contextual BM25 + reranker — reduces incorrect chunk retrieval rates by **up to 67 %** (5.7 % → 1.9 %); contextual embeddings alone provide 35 %, adding contextual BM25 reaches 49 %, and the reranker closes the rest.

#### A3. Self-improvement / self-evolution — safely

Model Omni's self-improvement on the **Darwin Gödel Machine (DGM)** architecture from Zhang, Hu, Lu, Lange, and Clune (arXiv:2505.22954, May 2025): an **archive** of agent variants, each proposing self-modifications, with empirical validation in **per-agent Docker sandboxes** before any change is promoted. From the paper: *"Each self-modification requires... empirically validate self-modifications against a benchmark, allowing the system to improve and explore based on observed results"* and *"a system of creating individual Dockerfiles for each agent to train, then subsequently retrieving results and destroying the temporary container... a necessary ethical precaution for potentially unwanted and dangerous behavior if left completely unmonitored."* [arXiv](https://arxiv.org/html/2602.05848v1)

Concrete safety harness for Omni's self-mod engine — **enforce all of these as non-bypassable**:

1. **Three-layer scope hierarchy** of what the agent may rewrite: (a) prompts/skills (low risk, auto-promote on green), (b) tool definitions / new MCP servers (medium risk, require unit-test + human review), (c) Python source code (high risk, full CI gate + 2-person human approval).
2. **Sandbox per proposal.** Spin a fresh container with no host network access, mount only the test harness; destroy after eval (DGM pattern).
3. **Benchmark-gated promotion.** Maintain a fixed regression suite — (a) functional benchmarks specific to Omni's domain, (b) safety benchmarks (refusal, prompt-injection resistance, privacy), (c) a held-out canary set the agent cannot see. Use a Hoeffding-style error-bounded acceptance test (as the DGDM follow-on paper does) rather than single-run improvement — a proposal must beat the parent by Δ with statistical confidence.
4. **Archive + diversity.** Keep the full lineage as JSON, never overwrite the parent (DGM "stepping stones"). [arXiv](https://arxiv.org/html/2505.22954v2) This both supports rollback and avoids catastrophic forgetting.
5. **Cryptographic immutability** of the safety harness itself: the validation code, benchmark suite, and approval-gate logic are stored in a separate repo signed with hardware-key approval; the agent has no write access to them.
6. **Human-in-the-loop kill switch** wired to a hardware deadman: pulling it freezes all autonomous loops and reverts to the last human-approved variant.
7. **Telemetry:** every proposed modification, accept/reject decision, and post-promotion drift gets emitted to a tamper-evident log (append-only WAL or hash-chain) that an external auditor can review.

**Honest framing:** outside narrow benchmarks (SWE-bench, drug-discovery pipelines as in the August 2025 DGDM bioRxiv preprint), recursive self-improvement is still research-grade. Omni should ship with self-improvement *off* by default; users enable it per scope and per environment.

#### A4. Multi-agent orchestration

Use the **supervisor / worker** pattern (Anthropic's "orchestrator-workers"): a single supervisor LLM that owns the user-visible state machine and delegates to specialised workers — Coder, Researcher, Voice, Vision, FileOps, DesktopUse, ScheduledTask, Verifier. Communication is **typed message passing on LangGraph edges**, not free-form chat (AutoGen's chat-loop approach uses *"10-15x more tokens than single agents"*, per Anthropic's own framework guidance PDF). For long-running plans, persist supervisor state to Postgres so a crash mid-plan resumes cleanly.

Coordination rules drawn from production deployments:

- **One LLM, one job per node.** No node should be both planning and acting.
- **All worker outputs validated** by a typed schema (Pydantic) before the supervisor consumes them.
- **Concurrency caps** to avoid token-cost explosions; an evaluator–optimiser loop must have a max-iterations stop.
- **Observable by default.** Wire Langfuse or LangSmith from day one for traces, evals, and cost.

#### A5. On-device / local LLM hosting on Android

The mobile LLM stack in mid-2026 is finally good enough that a useful assistant can run entirely on-device on flagship phones. The four runtimes to consider, with concrete numbers from primary sources:

| Runtime | Embeddable in APK? | Best models | Measured decode (tok/s) | Notes |
|---|---|---|---|---|
| **Google AI Edge / Gemini Nano (AICore)** | Yes — shared system service, ~0 KB APK weight | Gemini Nano 3 / 4 (Pixel/Samsung/Xiaomi/Vivo flagships) | Nano 3: ~9.6; Nano 4 Fast: ~19.1; Nano 4 Full: ~5.3 (Pixel 10 Pro XL, Tensor G5 TPU; Robert Triggs, Android Authority, Apr 2026 — character-counted, ±20 %) [androidauthority](https://www.androidauthority.com/gemini-nano-4-benchmarks-3655763/) | Best privacy/UX but locked to supported devices, prompt classifiers, foreground-only [Google](https://developers.google.com/ml-kit/genai) |
| **LiteRT-LM (successor to MediaPipe LLM Inference API)** | Yes, full APK embedding | Gemma 3 1B (529 MB int4), Gemma 3n E2B/E4B, Qwen2.5-1.5B, Phi-4-mini | Gemma 3 1B int4 on S24 Ultra: exactly **47 tok/s decode CPU / 56 tok/s decode GPU** (322 / 2,585 tok/s prefill, 1,138 / 1,205 MB RAM, 529 MB model [huggingface](https://huggingface.co/litert-community/Gemma3-1B-IT) — *litert-community/Gemma3-1B-IT* HF card, dynamic_int4 QAT, 2,048-token context); [Google Developers](https://developers.googleblog.com/en/gemma-3-on-mobile-and-web-with-google-ai-edge/) on S25 Ultra NPU: **85 tok/s decode, 5,836 tok/s prefill, 626 MB RAM**; [huggingface](https://huggingface.co/litert-community/Gemma3-1B-IT) Qwen2.5-1.5B int8 GPU: 30.9 tok/s; [huggingface](https://huggingface.co/litert-community/Qwen2.5-1.5B-Instruct) Gemma 3n E4B GPU on S24 Ultra: 9.4 tok/s [huggingface](https://huggingface.co/google/gemma-3n-E4B-it-litert-lm) | Production stack of choice; supports CPU/GPU/NPU; [Meet Prajapati](https://meetprajapati.com/blogs/running-on-device-ai-models-android-mediapipe-llamacpp-executorch/) MediaPipe LLM Inference API marked deprecated in favour of LiteRT-LM [Google AI](https://ai.google.dev/edge/mediapipe/solutions/genai/llm_inference/android) |
| **MLC-LLM (TVM + OpenCL/Vulkan)** | Yes, APK-embeddable; custom models need MLC compile toolchain [promptquorum](https://www.promptquorum.com/power-local-llm/best-local-llm-apps-android-2026) | Qwen3-1.7B, Phi-4 Mini, Llama 3.2 | MLC Chat on S25 Ultra (NPU path): Qwen3-1.7B ~40 tok/s; Phi-4-mini ~22 tok/s [promptquorum](https://www.promptquorum.com/power-local-llm/best-local-llm-apps-android-2026) (PromptQuorum blog, May 2026 — indicative) | Best CPU-fallback portability; Adreno _0 weight layout outperforms _1 layout [Callstack](https://www.callstack.com/blog/profiling-mlc-llms-opencl-backend-on-android-performance-insights) |
| **llama.cpp (GGUF)** | Yes (via llama.rn / native bindings — what PocketPal AI ships); Ollama variant requires Termux from F-Droid | Any GGUF: Qwen3, Gemma 3, Llama 3.2, Phi-4 | Phi-4-mini under PocketPal/Vulkan: ~16 tok/s; under Maid/Vulkan: ~18 tok/s; under Ollama/Termux CPU: ~10 tok/s (S25 Ultra) [promptquorum](https://www.promptquorum.com/power-local-llm/best-local-llm-apps-android-2026) | Most flexible, largest model catalogue, [Meet Prajapati](https://meetprajapati.com/blogs/running-on-device-ai-models-android-mediapipe-llamacpp-executorch/) weakest NPU story |

**RAM thresholds (synthesised from Google's litert-community cards + Google Developers Blog *Introducing Gemma 3n*):**
- **8 GB phones:** stick to 1B–2B Q4 (Gemma 3 1B at 530 MB; Qwen3 1.7B; Llama 3.2 1B) → 10–50 tok/s.
- **12 GB phones (S25 Ultra base, OnePlus 13, Pixel 10 Pro):** comfortably run 3B–4B Q4 (Phi-4 mini ~2.7 GB, Gemma 3n E4B at ~3 GB minimum) → 7–35 tok/s.
- **16 GB phones:** can host 7B Q4 in a pinch (~8–10 tok/s), but 3–4B remains the comfort zone.

Google's own statement on minimum hardware (Gemma 3 launch blog, March 12 2025): *"For best performance with Gemma 3 1B, we recommend a device with at least 4 GB of memory"*; [Google Developers](https://developers.googleblog.com/en/gemma-3-on-mobile-and-web-with-google-ai-edge/) and on Gemma 3n: *"While their raw parameter count is 5B and 8B respectively, architectural innovations allow them to run with a memory footprint comparable to traditional 2B and 4B models, operating with as little as 2 GB (E2B) and 3 GB (E4B) of memory"* (Google Developers Blog, *Introducing Gemma 3n: The developer guide*).

**Recommendation for Omni's Android shell:**
- **Tier 1 — flagships with AICore:** prefer **Gemini Nano via ML Kit GenAI APIs** for summarisation/proofread/rewrite features (zero APK bloat, OS-managed updates, system-level safety classifiers). Note quota: *"GenAI API inference is permitted only when the app is the top foreground application"* [Google](https://developers.google.com/ml-kit/genai) (Google ML Kit docs) — background tasks must fall back to a remote model.
- **Tier 2 — broad Android coverage:** embed **LiteRT-LM** with Gemma 3 1B (int4 QAT) as the default fallback; this gives 47–56 tok/s decode and a fixed ~1.1 GB RSS, acceptable on any 6–8 GB phone.
- **Tier 3 — power users:** ship an "advanced" toggle that downloads larger GGUFs (Qwen3-4B, Gemma 3 4B) and runs them via **llama.rn**; offer Termux + Ollama instructions for users who want full Linux on-device.

This avoids requiring Termux for normal users — *"Install Termux from F-Droid (not the Play Store version — the Play Store build is outdated and breaks Ollama installs)"* [promptquorum](https://www.promptquorum.com/power-local-llm/best-local-llm-apps-android-2026) (PromptQuorum) is too high a bar for a consumer app.

#### A6. Usability and multimodality

**Voice loop target:** end-to-end **sub-500 ms mouth-to-ear**, which is the human conversational window (Introl 2025 voice infra guide). Architect Omni's voice mode as a **cascading pipeline** by default (more controllable, easier to debug) with an optional **speech-to-speech** mode using OpenAI's gpt-realtime-1.5 (Feb 2026), Gemini 3.1 Flash Live, or Amazon Nova 2 Sonic (GA on Bedrock Dec 2 2025, ~$0.017/min) [Softcery](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture) for premium users.

Recommended building blocks:

| Stage | Cloud default | Local default | Latency |
|---|---|---|---|
| VAD | Silero VAD | Silero VAD | 85–100 ms [DEV Community](https://dev.to/programmerraja/2025-voice-ai-guide-how-to-make-your-own-real-time-voice-agent-part-3-3ocb) |
| ASR | Deepgram Nova-3 streaming (6.84 % median WER, sub-300 ms latency at $0.0077/min, "54.2 % improvement over the next-best alternative at 14.92 %" — Deepgram *Introducing Nova-3* blog) | faster-distil-whisper-medium or Whisper-Large-v3-Turbo | 200–300 ms |
| LLM | Claude Haiku 4 or Gemini Flash (350–400 ms TTFT) | Llama 3.x via Ollama | 200–500 ms TTFT |
| TTS | ElevenLabs Scribe v2 / Cartesia Sonic-3 (~75 ms TTFT) | Kokoro or Piper streaming | 75–200 ms |

Critical UX details: **barge-in** (echo cancellation + VAD + immediate TTS cancellation within 200 ms), [DEV Community](https://dev.to/programmerraja/2025-voice-ai-guide-how-to-make-your-own-real-time-voice-agent-part-3-3ocb) **streaming both ways** (start TTS on first LLM token; OpenAI calls this the basis of "voice-to-action"), [OpenAI](https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/) and **persistent session memory** so the user doesn't have to repeat context.

**Multimodality (2026 baseline an assistant must have):**
- **Vision:** Claude 4.6 Sonnet / Gemini 2.x / GPT-4o-class vision for screenshot+document analysis; local fallback: Qwen2.5-VL-3B or Gemma 3n (natively multimodal — *"Support for image or audio input is available with Gemma 3n"*, [Google AI](https://ai.google.dev/edge/mediapipe/solutions/genai/llm_inference/android) Google AI Edge docs).
- **Image generation:** Flux 1.1 Pro / Imagen 4 / GPT-image-1 via API; local: Stable Diffusion 3.5 / SDXL Turbo via diffusers.
- **Audio generation:** ElevenLabs / Suno / Stable Audio.
- **Video generation:** Veo 3 / Sora-2 / Runway Gen-4 via API (no realistic local option in 2026).
- **Document generation:** native to the LLM (markdown → Pandoc → PDF/DOCX); Anthropic Agent Skills (March 2026 open standard) is the right packaging format.

**FastAPI architecture for Omni:**
- WebSocket endpoints for streaming (voice + tokens).
- Server-Sent Events for non-voice streaming.
- All tools exposed as MCP servers (`/mcp/{server_name}` route), authenticated via OAuth 2.1 with PKCE — *"the MCP ecosystem has coalesced around OAuth 2.1 as the gold standard for authorization... Per-Client Consent... PKCE is required to prevent authorization code interception"* [Medium](https://gregrobison.medium.com/the-model-context-protocol-the-architecture-of-agentic-intelligence-cfc0e4613c1e) (Greg Robison, Medium, 2025).
- Postgres + pgvector for state and small-scale RAG; Qdrant for production-scale RAG; LanceDB for the embedded/desktop mode.
- Ollama and llama.cpp as **local LLM providers behind the same provider interface** as Anthropic/OpenAI/Gemini, so the same agent code runs against cloud or local.
- **Telemetry from day one:** Langfuse self-hosted for traces + evals; OpenTelemetry → Loki/Grafana for ops; per-conversation token/latency/tool-success metrics feeding the self-improvement evaluator.

---

### PART B — Shims Enterprise

#### B1. The pharma software stack — six acronyms and how they wire together

| Acronym | What it does | What Shims Enterprise must include |
|---|---|---|
| **ERP** (Enterprise Resource Planning) | Finance, procurement, AP/AR, costing, payroll, inventory financials, [Sarjen](https://process-xe.sarjen.com/2025/02/17/unlock-seamless-pharma-manufacturing-with-ebmr-integration-data-collaboration/) GST/e-invoice | Native India GST + multi-currency for exports |
| **MES** (Manufacturing Execution System) | Shop-floor execution, recipe management, equipment status, **eBR/eBMR** (electronic batch records), in-process checks, line clearance | Per Siemens Opcenter / Tulip / Vimachem reference architectures |
| **LIMS** (Laboratory Information Management System) | Sample login, test workflow, instrument integration, [SavvycomSoftware](https://savvycomsoftware.com/blog/lims-software-in-pharma/) COA, stability protocols, OOS management | Must integrate ELN, CDS (chromatography), [Amplelogic](https://amplelogic.com/gamp-solutions/laboratory-information-management-system) and balance feeds |
| **QMS** (Quality Management System) | Deviation, CAPA, change control, complaint, audit, supplier qualification, training, [AmpleLogic](https://www.amplelogic.com/blog/integrating-qms-with-erp-lms-and-lims-can-boost-compliance-and-efficiency-by-70-percent-in-2025) PQR/APQR | Workflow engine with electronic signatures |
| **DMS** (Document Management System) | Controlled SOPs, master batch records, specifications, versioning, training-effectiveness records | Version-locked, signed, periodic-review reminders |
| **RIM** (Regulatory Information Management) | Product registrations, dossier authoring, **eCTD** publishing, submission tracking, health-authority correspondence, IDMP | eCTD v4.0; CDSCO, US FDA, EMA, WHO submission gateways |

These must share **one identity model (one user, one e-signature, one role)**, **one tamper-evident audit-trail substrate** (append-only event log), and **one master-data spine** (product, material, supplier, equipment, location). Per AmpleLogic's reference description: *"One database powering all 14+ modules. Zero point-to-point integrations. Real-time data sharing across quality, lab, and plant. Pre-validated against 21 CFR Part 11, Annex 11, GAMP 5, and EU GMP guidelines from day one."* [AmpleLogic](https://www.amplelogic.com/) The most common failure mode for J.K. Lifecare-scale firms is buying best-of-breed components from different vendors and discovering at audit that the audit trails do not reconcile across systems.

#### B2. GxP / data integrity — what the software MUST do

The non-negotiable feature list driven by 21 CFR Part 11 + EU Annex 11 + WHO data integrity + Revised Schedule M + ALCOA+ is:

1. **Validated computer systems (CSV).** GAMP 5 risk-based approach; URS → FS → DS → IQ/OQ/PQ; revalidation triggered by change control. Annex 11 (2011 + 2025 draft revision) explicitly: *"Computerised systems should be qualified and validated… The validation strategy and effort should be determined based on quality risk management."* [Europa](https://health.ec.europa.eu/document/download/40231f18-e564-4043-94de-c031f813d38b_en?filename=mp_vol4_chap4_annex11_consultation_guideline_en.pdf)
2. **Tamper-evident audit trail** that captures, per 21 CFR 11.10(e): who, what (old value, new value), when (timestamped to a synchronised clock), where, and **why** for every GxP-relevant write. *"Record changes shall not obscure previously recorded information"* [eCFR](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11) (21 CFR 11.10(e), verbatim). Audit trails must be reviewed before batch release — the "review by exception" pattern.
3. **Electronic signatures** uniquely linked to one human, including printed name + date/time + meaning of signing, [Cognidox](https://www.cognidox.com/blog/what-is-fda-21-cfr-part-11) and not transferrable (21 CFR 11.50, 11.70, 11.100, 11.200). A single signing event = two distinct authentication factors at first signing of a session.
4. **Role-based access control** with segregation of duties (the maker cannot be the checker) and a documented user-access matrix.
5. **Backup, restore, archive, business continuity.** Annex 11 §16 requires tested restore; quarterly DR drills; retention per record class (some Indian export records must be kept 5+ years post-shelf-life).
6. **Change control over the system itself**, including over AI models — every model version, every prompt change, every tool change becomes an entry in the QMS change-control register.
7. **ALCOA+ on the data:** Attributable, Legible, Contemporaneous, Original, Accurate + Complete, Consistent, Enduring, Available (MHRA 2018; FDA 2018 Data Integrity Q&A). [Qmsdoc](https://qmsdoc.com/2026/01/24/understanding-alcoa-the-foundation-of-data-integrity-in-the-pharmaceutical-industry/) Modern variant **ALCOA++** adds Traceable (PIC/S PI 041) — every derived value must trace to source.
8. **Periodic review and access review.** Annex 11 expects evidence that the controls still work.

**What this means for "full autonomy":** an AI cannot legally be the named approver for an action that requires a Qualified Person signature. The signature must be linked to an accountable human. Shims Enterprise's autonomy toggle therefore has hard guards (see B5) — AI may **prepare, recommend, and pre-fill**, but the human signs.

#### B3. Function-by-function module design

For each function: key processes, records, AI opportunities, and what may safely be autonomous.

**(a) R&D & formulation development**
- **Records:** project files, literature, lab notebooks (ELN), DoE plans, raw materials sourced for trials, IP disclosures.
- **AI opportunities:** literature search agent, retrosynthesis suggestion (IBM RXN-style), in-silico screening, Bayesian DoE optimisation, automated lab-report drafting. The Darwin-Gödel approach has been applied to drug-discovery code (DGDM, bioRxiv August 2025).
- **Safely autonomous:** literature ingestion, DoE planning, report drafting for human review.
- **Not autonomous:** patent filings, novel compound synthesis decisions, GLP-relevant data signoff.

**(b) Scale-up / tech transfer / process development**
- **Records:** Process Development Reports (PDR), scale-up risk assessments, equipment fit-for-purpose, master batch record (MBR) drafts, validation plans (PV1/2/3), tech-transfer protocol & report.
- **AI opportunities:** dimensional analysis & scale-up calculators, predictive process modelling (PSE simulation linked to AI surrogate), automated PDR drafting from R&D ELN data, gap analysis between R&D and commercial site GMP status.
- **Safely autonomous:** drafting tech-transfer documents, simulation runs, risk-assessment first pass.
- **Not autonomous:** approval of MBRs and validation protocols (QA, named individual).

**(c) QC + QA**

QC (testing): sample login, test assignment, instrument integration (HPLC/GC/IR/UV/Karl Fischer), result entry, OOS investigation, COA generation, stability programmes (ICH Q1A protocols, storage stations, pull schedule).
- **AI opportunities:** AI-driven chromatographic peak picking and integration review (still requires human approval per FDA Warning Letters on integration manipulation — *"Manually adjusting integration parameters on chromatography data to move a result inside a specification limit"* [TotalLab](https://totallab.com/resources/alcoa-principles/) is a top inspection finding); spectral analysis (NIR/Raman) for identity confirmation; OOS triage agent that drafts the investigation plan referencing prior similar OOS events.
- **Safely autonomous:** sample login from GRN, test assignment, scheduling stability pulls, instrument-status checks, **first-pass** OOS triage.
- **Not autonomous:** OOS final disposition, integration parameter changes, retest decisions — all require named QC analyst + QA approval with full ALCOA+ traceability.

QA: deviation, CAPA, change control, document control, batch release, PQR, supplier qualification, complaint handling, internal audit, training.
- **AI opportunities:** deviation classification + impact assessment draft, CAPA effectiveness prediction, change-control impact mapping, APQR auto-aggregation from MES+LIMS+QMS (AmpleLogic's APQR module is a reference — *"Cloud-based APQR software that automatically aggregates manufacturing, quality, and laboratory data across LIMS, eQMS, MES, ERP, and DMS to generate compliant PQR reports"*). [AmpleLogic](https://www.amplelogic.com/)
- **Safely autonomous:** APQR data aggregation and draft generation, deviation triage, training reminder/assignment, document review-due alerting.
- **Never autonomous:** **batch release**, CAPA closure, change-control approval, deviation closure, complaint closure, supplier qualification approval — all explicitly named-human under 21 CFR 211.22, EU GMP Chapter 2, Schedule M.

**(d) Production / manufacturing (MES + eBR)**

Per Siemens Opcenter, Tulip, and Vimachem reference architectures, the eBR must: enforce the master batch record; [Siemens](https://www.sw.siemens.com/en-US/technology/electronic-batch-record/) require electronic signatures per step (21 CFR Part 11-compliant); pull recipe data from ERP; [IntuitionLabs](https://intuitionlabs.ai/articles/electronic-batch-records-biotech-gxp-guide) pull dispensing/weighing values from connected scales via OPC-UA or REST; enforce line clearance and material-status checks; capture exceptions with mandatory comment; support **review by exception**.

- **AI opportunities:** predictive maintenance on tablet presses (vibration/acoustic — *"High-frequency vibration or acoustic sensors are placed on the turret or press frame. The AI is trained to recognize the specific acoustic/vibration signature of 'good' compressions. As punches wear, this signature changes subtly"*, [F7i](https://f7i.ai/blog/beyond-the-buzz-7-real-world-ai-predictive-maintenance-use-cases-in-pharma-for-2025) F7i.ai 2025); Model Predictive Control for granulation/coating; in-line PAT (NIR/Raman) with multivariate SPC; AI assistant that pre-fills exception comments.
- **Safely autonomous:** line-clearance checklists with sensor confirmation, in-process check scheduling, equipment-status monitoring, **closed-loop control of non-critical process parameters** within tight pre-validated bands, predictive-maintenance work-order generation.
- **Not autonomous:** any change to a validated critical process parameter outside its proven acceptable range; batch certification; release decisions.

**(e) Warehouse / inventory / materials**
- **Records:** GRN with sampling, quarantine status, COA-linked release, FEFO/FIFO picking, dispensing log per batch, returns, rejects, recall holds, customs/export documents.
- **AI opportunities:** GRN auto-population from supplier ASN/e-invoice JSON, computer-vision drum-label OCR for verification against PO, anomaly detection on weights, automated reorder-point recalculation, demand forecasting (Prophet / temporal-fusion transformer).
- **Safely autonomous:** GRN draft creation, FEFO pick suggestions, cycle-count scheduling, reorder triggers (with cap), expiry monitoring.
- **Not autonomous:** final material release from quarantine (QC/QA signature required under Schedule M).

**(f) Accounts / finance — India + export**

The **India GST e-invoice + e-Way Bill stack** is non-negotiable for any sale in India and most B2B exports. Key implementation facts (from the official IRIS IRP / NIC portal advisories):
- **Threshold:** B2B e-invoicing mandatory for AATO > ₹5 Cr; J.K. Lifecare is certainly in scope.
- **Reporting time limit:** From 1 Apr 2025, taxpayers with AATO ≥ ₹10 Cr **must report invoices to the IRP within 30 days** of invoice date — *"If an e-Invoice is reported beyond the 30-day limit, the system will restrict IRN generation"* [IRIS IRP](https://einvoice6.gst.gov.in/content/api-integration/) (IRIS IRP advisory).
- **MFA mandatory:** *"MFA setup is now mandatory on the IRP Portal. Upon logging in with your credentials, you will be prompted to register for MFA. Login will not be permitted until MFA registration is successfully completed."* [IRIS IRP](https://einvoice6.gst.gov.in/content/api-integration/)
- **API access tiers:** direct API access only for taxpayers with turnover above the threshold or those registered for e-Way Bill; [Microsoft Learn](https://learn.microsoft.com/en-us/dynamics365/finance/localizations/india/apac-ind-e-invoices) everyone else routes through a **GSP** (GST Suvidha Provider — IRIS, ClearTax, Masters India, Precision e-Tech etc.).
- **Token lifecycle:** *"Access tokens are short-lived (6 hours) and must be refreshed without interrupting your workflow"* [PrecisionTech](https://precisiontech.in/solutions/gst-gsp-api/) (PrecisionTech); *"All payloads transmitted to/from GSP APIs are encrypted using a session-specific key derived from the GSP's public key and your application key."* [PrecisionTech](https://precisiontech.in/solutions/gst-gsp-api/)
- **E-Way Bill:** required for goods movement > ₹50,000 within India; [Tirnav](https://tirnav.com/blog/e-way-bill-and-e-invoice-api) generated from the same NIC system, often by the transporter via Part B of the EWB.
- **New for 2026:** validations effective 1 Jan 2025 for E Way Bill APIs; restriction on extension of EWBs beyond 360 days; [Einv-apisandbox](https://einv-apisandbox.nic.in/) new 40 % GST rate slab added to IRP tax-rate master. [IRIS IRP](https://einvoice6.gst.gov.in/content/api-integration/)

**Architecture recommendation:** integrate via a **GSP** (don't build direct IRP integration unless the dev cost ≈ ₹5 lakh is justified — *"the cumulative value of direct integration will work out to approximately Rs 5 lakh for the enterprise"*, [Cleartax](https://cleartax.in/s/e-invoicing-api-integration-modes) ClearTax). Build the finance module's invoice service to generate the IRP JSON schema, push to GSP, receive the IRN+QR, store the signed e-invoice, and trigger EWB Part A automatically.

- **AI opportunities:** auto-coding of supplier invoices to GL via vendor-specific learning; GSTR-2B vs purchase reconciliation (4–12 hours/month per accountant pre-automation, [PrecisionTech](https://precisiontech.in/solutions/gst-gsp-api/) per PrecisionTech); cash-flow forecasting; export-incentive optimisation (RoDTEP, drawback).
- **Safely autonomous:** invoice creation from MES sales orders, IRN generation, EWB Part A generation, GSTR-2B reconciliation, payment reminders.
- **Not autonomous:** payment release (board-defined limits → human approval), GL adjustments, year-end closure.

**(g) Market intelligence**
- **Records:** competitor pricing, API DMF/CEP filings, USP/EP monograph changes, market shortage data (FDA Drug Shortages, CDSCO), customer pipeline intel.
- **AI opportunities:** RA-Omni research agent that monitors FDA/EMA/CDSCO RSS feeds, parses competitor DMF lists, summarises monograph changes, generates weekly intel digest.
- **Safely autonomous:** monitoring and digest generation, suggestion of new market opportunities; **never** autonomous: pricing decisions, market entry/exit.

**(h) Sales / CRM / order management**
- **Records:** customer master, contracts (especially supply agreements with quality terms), price lists by market, orders, samples, complaint linkages to QMS.
- **AI opportunities:** lead enrichment, contract-clause review (especially quality-agreement obligations that affect manufacturing), proposal drafting, churn prediction.
- **Safely autonomous:** quotation drafting, sample dispatch coordination, order acknowledgement, shipment tracking, COA bundling.
- **Not autonomous:** new customer onboarding (KYC + due diligence for diversion-risk countries), credit limits, contractual quality-terms approval.

**(i) Regulatory Affairs**
- **Records:** product registrations per market (with status, validity, variations history), DMF/CEP files, eCTD sequences, health-authority correspondence, commitments register, IDMP data.
- **AI opportunities:** auto-drafting Module 3 quality sections from MES/LIMS/QMS data; gap analysis between CDSCO/USFDA/EMA expectations; eCTD validation; variation-impact assessment. Vendor reference: Veeva Vault RIM, IQVIA SmartSolve RIM, ArisGlobal LifeSphere, EXTEDOpulse, Kivo for SMEs (*"Kivo RIM was built with submission-readiness at its core"*). [Kivo](https://kivo.io/rim)
- **eCTD landscape (2026):** **eCTD v4.0** is now rolling out across major markets; [IntuitionLabs](https://intuitionlabs.ai/articles/rim-systems-idmp-standards-guide) FDA has been accepting v4.0 since 2023, EMA piloting it, CDSCO accepts eCTD-based submissions for some import/registration pathways but still has paper requirements for many domestic dossiers. The system must publish v3.2.2 and v4.0 in parallel.
- **Safely autonomous:** dossier outline generation, Module 3 first-draft from internal data, eCTD validation, submission tracking, commitments-due alerts.
- **Not autonomous:** submission sign-off, response to deficiency letters, regulatory strategy decisions.

#### B4. India-specific — CDSCO + GST + export

**CDSCO Revised Schedule M (Gazette G.S.R. 922(E), 5 Jan 2024)** — explicit additions vs old Schedule M:
- Pharmaceutical Quality System (PQS — ICH Q10-aligned) [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Quality Risk Management (QRM — ICH Q9) [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Product Quality Review (PQR — annual product review)
- Qualification and validation lifecycle [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Sanitation and hygiene [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Computerised systems (ALCOA+ aligned)
- Change control, deviation, CAPA explicitly required [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Self-inspection and quality audit
- Complaints, recalls, returned goods [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- Product development reports, validation, pharmacovigilance, post-marketing studies [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)

**Compliance timeline (per India Briefing + Qvents reporting on G.S.R. 922(E) and G.S.R. 127(E)):**
- Large manufacturers (turnover > ₹250 Cr): 28 Jun 2024 (effective). [India Briefing](https://www.india-briefing.com/news/india-extends-gmp-compliance-deadline-to-december-2025-35679.html/)
- MSMEs (turnover ≤ ₹250 Cr): originally 28 Dec 2024, extended to **31 Dec 2025** *only* for those that applied via Form A by 11 Apr 2025; *"only 10–12 % of manufacturing firms applied for an extension. Consequently, the remaining firms will now be subject to immediate and comprehensive inspections under the revised requirements"* [Vaayath](https://vaayath.com/cdsco-revised-schedule-m-inspections-2025/) (Vaayath Consulting, 2025).

**Export documentation that the system must produce:** WHO-GMP certificate, CoPP (Certificate of Pharmaceutical Product), free-sale certificate, written confirmation for APIs to EU (per EU Falsified Medicines Directive Article 46b), DMF/ASMF/CEP letters of access, RoDTEP/drawback documents, shipping bills + EWB + e-invoice, country-specific health certificates.

**US FDA + EU GMP + WHO-GMP overlay:** the same site (Active Pharmaceutical Ingredient manufacturer) typically holds multiple approvals. The software must support **one EBR** that produces the records satisfying all simultaneously — meaning the strictest interpretation wins on audit trail, signatures, validation, and data retention. EU GMP Annex 11 plus the 2025 draft revision: *"Computerised systems should be qualified and validated… Risks associated with the use of computerised systems in GMP processes, will not impact product quality, patient safety or data integrity"*; 21 CFR Part 11 §11.10 controls; WHO TRS 1019 Annex 5 (data integrity).

#### B5. Phased roadmap + the autonomy toggle

**Phased build sequence (24-month plan for J.K. Lifecare's expansion from 5 departments to a full autonomous factory):**

| Phase | Months | Modules | Why this order |
|---|---|---|---|
| 0 — Foundation | 0–3 | Identity + e-sig + audit-trail substrate; CSV framework; URS for each module; gap analysis against Revised Schedule M | Without the GxP substrate nothing else is compliant |
| 1 — Extend current 5 depts | 3–9 | **DMS + QMS first** (deviation/CAPA/change control), then upgrade **LIMS** (stability + OOS workflows), then production EBR/eBMR | These are the most-cited Schedule M gaps; biggest audit risk reduction per rupee |
| 2 — Finance + sales | 9–15 | **ERP (India GST + IRP/EWB + multi-currency)**, **Sales/CRM** with COA bundling and order management | Cash-impact and customer-impact modules; needed before scaling exports |
| 3 — Tech transfer + scale-up | 12–18 | Scale-up module, **Tech-transfer workflow**, predictive maintenance, AI-PAT on a pilot line | Lets new molecules move from R&D → commercial faster |
| 4 — RA + RIM | 15–21 | **RIM with eCTD v3.2.2 + v4.0 publishing**, DMF/CEP management, submission tracker | Enables new market filings; depends on Module 3 data from earlier phases |
| 5 — Markets + autonomy hardening | 18–24 | Market-intel agent, advanced AI/ML modules, autonomy-level toggle, kill-switch infrastructure, formal validation of AI features | Done last because autonomy on a non-validated base is illegal |

**The autonomy toggle — concrete design:**

Expose **five graduated levels per workflow** (not a single global switch), persisted in the QMS as a configuration item under change control:

- **L0 — Shadow.** AI runs invisibly; output stored for evaluation; no action taken. Used for new features.
- **L1 — Suggest.** AI recommends; human must approve; default for all GxP-impacting workflows.
- **L2 — Pre-fill + confirm.** AI executes the action but it is held in "pending" state; human single-click confirm to commit. Default for non-GxP-critical workflows after evaluation passes.
- **L3 — Auto-execute with human notification.** Action commits immediately; humans are notified and can roll back within a defined window. Allowed for clearly low-risk loops (cycle counts, reorder triggers below cap, document review-due reminders, GRN drafting).
- **L4 — Lights-out.** No human in loop. **Only permitted** for workflows formally validated to be both low-risk and fully reversible (dashboard generation, log file cleanup, non-GxP report scheduling).

**Hard-coded never-autonomous list** (system-level constants enforced in code, not configuration; changing them requires a signed source change + full revalidation):
- Batch release / certification
- OOS final disposition
- Deviation closure
- CAPA closure / effectiveness verification
- Change-control approval
- Supplier qualification
- Material release from quarantine
- Regulatory submission sign-off and response to deficiency letters
- Any signature whose semantic meaning is "I approve" under 21 CFR 11.50, Annex 11 §14, or Schedule M PQS clauses
- Any change to a validated critical process parameter outside its pre-approved range
- Master batch record approval
- Customer onboarding (KYC)
- Payment release above ₹X (board-set)

**Kill-switch / safety architecture:**
1. **Soft kill** (per workflow): a UI button that toggles the workflow back to L1.
2. **Hard kill** (plant-wide): a physical deadman / single command that drops all autonomy to L0 across all modules, halts AI agents, but lets manual operations continue. Reachable from any operator station.
3. **Audit-trail-of-autonomy:** every autonomy-level change is itself a Part-11/Annex-11 event with reason, signature, and effective date.
4. **Pre-deployment validation per workflow:** before a workflow can be promoted to L3 or L4, formal CSV with predefined success criteria, including a quantitative risk assessment per ICH Q9 and FDA's 2025 AI credibility-assessment draft guidance. The validation evidence sits in DMS, linked to the change control that promoted it.
5. **Continuous monitoring:** any L3/L4 workflow has SLOs (success rate, drift, anomaly count) wired into the QMS; a breach auto-demotes back to L1.

---

## Recommendations

### For Shims Omni — staged next steps

1. **Weeks 0–4:** stand up a minimal LangGraph supervisor on FastAPI, with one MCP server (filesystem), Anthropic + OpenAI + Gemini + Ollama as interchangeable provider plugins, Qdrant for RAG, Langfuse for traces. Baseline benchmarks (latency, cost, tool-success) saved.
2. **Weeks 4–10:** add the cascading voice loop (Deepgram Nova-3 + Cartesia Sonic-3 in cloud mode; faster-distil-whisper + Kokoro for local). Target <600 ms mouth-to-ear on broadband.
3. **Weeks 10–16:** add Letta-style episodic memory + Mem0-style consolidation daemon; ship the Android shell with LiteRT-LM + Gemma 3 1B for mid-tier devices and AICore/Gemini Nano on supported flagships.
4. **Weeks 16–24:** ship the self-improvement engine in **prompt-only scope**, with sandboxed eval and a 2-person human-approval gate; benchmark each promoted prompt for ≥1 week before next.
5. **Promotion criteria for L3 autonomy (toggle move):** ≥99 % task success on the regression suite, ≥98 % refusal correctness on the safety set, no novel category of failure in the last 30 days, signed off by both engineering and a designated safety reviewer.
6. **Do not** open self-modification to Python source code until the prompt/skill loop has run 6 months without an unrecovered safety incident.

### For Shims Enterprise — staged next steps

1. **Months 0–3:** GAP analysis against Revised Schedule M and 21 CFR Part 11 of the current 5-department system. File any pending Form A extension if MSME; if past extension, plan for active inspection.
2. **Months 3–9:** ship the GxP substrate + DMS + QMS + LIMS upgrades; cutover production to eBR with full Annex 11 controls.
3. **Months 9–15:** integrate via a GSP (IRIS or ClearTax recommended) for IRP/EWB; ship ERP finance core with GST and multi-currency.
4. **Months 12–18:** introduce predictive maintenance on the highest-OEE production line; pilot AI-assisted OOS triage at L1 (suggest only).
5. **Months 15–21:** stand up RIM + eCTD publishing; first export filing via the new stack.
6. **Months 18–24:** introduce the autonomy toggle infrastructure; promote 3–5 low-risk workflows to L3 *only after* formal CSV.
7. **Promotion thresholds for any new L3/L4 autonomy on the factory side:** documented credibility assessment (per FDA 2025 draft), ≥6 months of L1/L2 shadow-pass data, zero critical deviations attributable to the AI, change-control approval by the QA Head named on the manufacturing licence.

---

## Caveats

- The Anthropic *"5 patterns"* framing is a 2024 December design note rephrased in 2025; production teams typically combine 2–3 patterns and customise heavily — treat the patterns as vocabulary, not architecture.
- The **Darwin Gödel Machine** result is on coding benchmarks (SWE-bench); generalisation to general-purpose self-improving agents is a research conjecture, not a proven result. Treat self-modification of Python source as a multi-quarter R&D investment, not a feature.
- **Mobile LLM benchmark numbers vary by ±30 %** between sources (Google litert-community HF cards are the most authoritative; PromptQuorum / Android Authority numbers are useful for direction but not absolute). Confirm on your target devices before committing to a UX promise.
- **Speech-to-speech models** (gpt-realtime-1.5 et al.) advertise lower latency but bill on a long-context-accumulation basis; total cost can be **~10× the cascading pipeline** for long conversations.
- **Schedule M enforcement is in flux.** CDSCO has been signalling that inspections will *not* wait for the formal December 2025 deadline [iFactory](https://ifactoryapp.com/industries/pharmaceuticals/schedule-m-revised-india-pharma-gmp-rules) — large manufacturers in extension status are being inspected now. Plan for active inspections from H2 2025 onward, not a graceful 2026 cutover.
- The **EU Annex 11 revision (draft 2025)** has not yet been finalised; it tightens the lifecycle, third-party, and risk-management expectations vs the 2011 text. Design to the draft, validate to the in-force version, and budget revalidation when it lands.
- **AI in pharma is regulated, not free.** The FDA's 2025 draft AI-in-pharma-manufacturing guidance requires a *risk-based credibility assessment*, static/deterministic models for critical processes, and explicit human-in-the-loop. [OXMaint](https://www.oxmaint.com/blog/post/ai-predictive-maintenance-regulatory-compliance-manufacturing-pharma-aerospace) EU AI Act puts AI in manufacturing quality control under "high-risk" with transparency, bias detection, and ISO 42001-aligned risk management obligations. [OXMaint](https://www.oxmaint.com/blog/post/ai-predictive-maintenance-regulatory-compliance-manufacturing-pharma-aerospace) ICH Q9(R1) is the binding QRM reference. Budget for AI-specific validation, not just CSV.
- **"Full autonomy" in a GMP factory is, today, illegal for the high-stakes decisions.** The autonomy toggle's "L4 lights-out" mode is real but narrow; do not market this product as "autonomous batch release."
- The five graduated autonomy levels are an opinionated design here, not an industry standard. Other reference frameworks (ISA-95 levels, SAE J3016 for vehicles) use different scales. Adopt this scale internally and document it under change control.