# SHIMS Simple Agent Architecture

A lightweight, easy-to-remember way to understand how SHIMS thinks and acts.

## The Three Layers

| Layer | What it does | SHIMS name | Analogy |
|-------|--------------|------------|---------|
| **1. Memory** | Finds relevant past info, files, web pages, and facts | `omni_brain` + RAG + mailbox/capture | Your notebook and bookmarks |
| **2. Brain** | Decides what to do: answer directly, search, run a tool, or ask you | `agent_loop` + `agent_wave` | You reading the request and picking a plan |
| **3. Hands** | Actually does the work: files, shell, web, images, code, etc. | `agent_tools` + media routers | Your keyboard, mouse, and browser |

## How One Turn Flows

```
User message
    │
    ▼
┌─────────────────┐
│  1. Memory      │  → Search long-term memory, conversation history,
│  (omni_brain)   │    local files, captures, mailbox for context.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. Router      │  → Decide the route: chat, web-search, media,
│  (supervisor)   │    agent-loop, or deterministic tool.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. Brain       │  → Plan waves of tool calls, run them, read results,
│  (agent_loop)   │    synthesize a final answer.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. Hands       │  → Execute tools: shell.run, fs.read, web.search,
│  (agent_tools)  │    image/audio/video generation, coder.spawn, etc.
└────────┬────────┘
         │
         ▼
   Final answer + trust envelope
```

## When SHIMS Uses Each Layer

| Situation | Layer that owns it | Example |
|-----------|-------------------|---------|
| Simple chat / common knowledge | Brain (direct LLM) | "What is 2+2?" |
| Needs current web facts | Memory + Hands (web.search) | "Latest AI news" |
| Needs a file or command | Hands (fs.* / shell.run) | "Read README.md" |
| Needs an image/audio/video | Hands (media tools) | "Generate a cat image" |
| Multi-step coding task | Brain + Hands (coder.*) | "Build a React app" |
| Ambiguous or risky action | Brain asks for approval | "Delete a folder" |

## The Wave Pattern (Simplified)

Instead of doing one tool at a time, SHIMS plans a **wave** — a batch of independent tool calls that can run in parallel.

1. **Plan**: "I need to search the web AND list files."
2. **Run**: Both tools execute at the same time.
3. **Read**: SHIMS reads both results.
4. **Decide**: Either run another wave or give the final answer.

This is why SHIMS can feel fast: independent tools don't wait for each other.

## Provider Stack (Local First)

SHIMS tries the closest, cheapest provider first:

1. **LM Studio** (`google/gemma-4-e4b`) — local GPU, fastest, private
2. **Ollama** — local CPU/GPU fallback
3. **Cloud providers** — Anthropic, OpenAI, Gemini, etc. (only if keys are set)

If one fails, the circuit breaker opens and the next one takes over.

## Trust Envelope

Every answer comes with a trust score:

- **verified** — tool output or ledger-backed artifact
- **memory-backed** — grounded in past conversation or RAG
- **inferred** — LLM-generated, may need verification

This tells you whether the answer is a fact or an educated guess.

## Key Takeaways

1. **Memory first** — SHIMS looks up what it knows before guessing.
2. **Router decides** — not every message needs a tool.
3. **Waves are parallel** — independent tools run together.
4. **Local first** — prefer LM Studio/Ollama; cloud is a fallback.
5. **Trust matters** — check the trust level for uncertain claims.
