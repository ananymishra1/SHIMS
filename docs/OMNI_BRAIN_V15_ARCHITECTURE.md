# SHIMS Omni Brain v15 Architecture

## Research Inputs

- OpenAI Responses now supports hosted `web_search` as a tool in the Responses API, including controls such as search context size and returned token budget: https://developers.openai.com/api/docs/guides/tools-web-search
- OpenAI image generation supports GPT Image models through image APIs and as Responses tools for supported models: https://developers.openai.com/api/docs/guides/image-generation
- OpenAI file search uses vector stores as managed knowledge bases: https://developers.openai.com/api/docs/guides/tools-file-search
- OpenAI background mode documents a pollable long-running response pattern for deep tasks: https://developers.openai.com/api/docs/guides/background
- LangGraph long-term memory guidance frames memory as persistent cross-thread storage, not only short-term chat history: https://docs.langchain.com/oss/python/langchain/long-term-memory

## Local SHIMS Design

SHIMS keeps the highest-control version of those ideas locally:

1. The live router still chooses tool-first, web-search, greeting, or LLM routes before model calls.
2. The Omni Brain database stores long-term memories, turn episodes, RAG chunks, web research results, and queued background-learning tasks in `data/state/omni_brain.sqlite3`.
3. Each turn retrieves packed memory/RAG/research context and injects it into the system prompt with the active agent id.
4. Search results are captured into the research table and also chunked into RAG, so one live search can improve later offline answers.
5. Generated artifacts are indexed after verification so SHIMS can remember what it created.
6. The background learning loop reviews telemetry and episodes, writes daily lessons, and queues improvement tasks.
7. Self-evolution remains guarded: proposals may be generated, but source changes still require sandbox validation and named human approval.

## Agent Roles Added

- RAG Knowledge Agent: indexes and retrieves local knowledge.
- Research Synthesizer Agent: combines web results, RAG, and citations.
- Background Learning Agent: turns telemetry and episodes into lessons and improvement tasks.
- Safety Governor Agent: applies autonomy, GxP, secret-handling, and artifact-verification rules.

## API Surface

- `GET /brain/status`
- `POST /brain/context`
- `POST /brain/ingest`
- `POST /brain/learn`
- `GET /brain/tasks`
- `GET /memory`
- `POST /memory/save`
- `GET /memory/search`
- `DELETE /memory/{memory_id}`
- `POST /rag/search`
- `POST /rag/ingest`
- `POST /web/plan`

## Search Planning Fix

The web layer no longer sends raw chat turns directly to search providers. A dedicated query planner now:

1. Removes wake words, politeness, and command scaffolding.
2. Preserves search operators such as `site:`, `filetype:`, `intitle:`, negative terms, and quoted phrases.
3. Compacts long natural-language requests into keywords.
4. Classifies the search intent as general, patent, regulatory, market, fresh, identifier, or URL.
5. Builds a small ordered variant list so the backend can retry a better query if the first form returns no results.
6. Stores successful search results into the Omni Brain RAG database for later offline grounding.
