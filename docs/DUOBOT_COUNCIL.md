# DuoBot Council of the Wises

The **Council of the Wises** is a DuoBot mode that brings up to five independent agents into the same conversation:

- **Omni** — the primary SHIMS instance (cloud or local, user-configured)
- **Gemini** — Google Gemini
- **Claude** — Anthropic Claude
- **OpenAI** — OpenAI GPT
- **Factory** — the Local Factory Ollama instance

Each member sees the user's request and the recent conversation history, then speaks in parallel. A **Chair** (defaults to Omni) reads every opinion and produces a final answer plus an optional action plan. The Chair can invoke any SHIMS agent tool — shell commands, file edits, plans, schedules, browser automation, mail, the App Factory, self-evolution patches, and more.

## Enabling Council mode

1. Open `/omni-duobot`.
2. Click **Council** in the header, or create a new conversation and type `council` when prompted for the mode.
3. Configure AI keys/models in **AI settings** if you have not already.

## Required API keys

Set these environment variables (or add them to your `.env`):

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Google Gemini
GOOGLE_API_KEY=...      # or GEMINI_API_KEY
GEMINI_MODEL=gemini-2.5-flash
```

If a key is missing, that council member will fall back to the local Ollama model configured for DuoBot.

## How a council turn works

1. You send a message.
2. Every enabled council member responds in parallel, using its own provider/model and persona.
3. The Chair synthesizes the discussion into:
   - a final answer shown to you, and
   - an optional list of `actions` (tool calls) to execute.
4. If `auto-execute` is enabled, the Chair runs the actions immediately. Otherwise, risky actions stop and appear in the **Pending Council Actions** panel for your approval.

## Action plan format

The Chair returns a JSON object like:

```json
{
  "final_answer": "We will create a daily log-summary plan and run it.",
  "actions": [
    {"tool": "plan.create", "args": {"goal": "Summarize yesterday's logs"}, "reason": "automate the summary"},
    {"tool": "schedule.create", "args": {"title": "Log digest", "schedule_type": "cron", "when": "0 8 * * *", "action_type": "plan", "payload": {"plan_id": "..."}}, "reason": "run every morning"}
  ]
}
```

Any tool in `shared/agent_tools.py` can be used.

## Safety and auto-execute

By default, council actions are **gated** exactly like normal agent-tool calls:

- Safe/read-only tools run immediately.
- Risky tools (shell writes, file edits outside scratch dirs, self-patches, package installs, etc.) require approval.

To let the Chair apply changes without asking, enable **one** of the following:

1. Per-DuoBot setting: check **AI settings → Council auto-execute**.
2. Global environment variable: `SHIMS_OMNIPOTENT_MODE=true`.

> **Warning:** `SHIMS_OMNIPOTENT_MODE=true` removes all approval gates across SHIMS, not just the council. Use it only on a machine you are willing to let the agent modify.

## Approving or rejecting pending actions

When the Chair proposes a gated action:

1. The action appears in the right-hand **Pending Council Actions** panel.
2. Click **Approve** to run just that action and continue with the rest of the action list.
3. Click **Reject** to cancel that action and every action after it.

## Customizing the council

In **AI settings** you can:

- Toggle **Council auto-execute**.
- Choose the **Council chair** (Omni, Gemini, Claude, OpenAI, or Factory).

To change the member list, edit the `council_members` field via the API or settings JSON. The default is `["primary", "gemini", "anthropic", "openai", "local"]`.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/duobot/conversations` | Create a conversation; set `mode: "council"` |
| POST | `/api/duobot/conversations/{id}/turn` | Run one council round |
| POST | `/api/duobot/conversations/{id}/council/approve` | Approve a pending action `{approval_id}` |
| POST | `/api/duobot/conversations/{id}/council/reject` | Reject a pending action `{approval_id}` |

## Files involved

- `shared/omni_duobot.py` — council turn loop, member calls, chair decision, action execution.
- `shared/duobot_routes.py` — REST endpoints.
- `frontend/omni_duobot.html` + `frontend/js/omni_duobot.js` — UI.
- `shared/agent_tools.py` — tool registry and gating.
