# OpenClaw ↔ SHIMS integration

Two plugins live on the OpenClaw side of this machine:

| Plugin | Where | What it does |
|---|---|---|
| `shims-enterprise-bridge` | `~/Documents/Codex/.../openclaw-enterprise-bridge/plugin` | Pre-existing. Exposes SHIMS Enterprise tools (`shims_probe`, `shims_enterprise_dashboard`, `shims_mailbox_status`, …) to OpenClaw. |
| `shims-channel-bridge` | `integrations/openclaw/shims-channel-bridge` (this repo) | New. Relays **inbound WhatsApp messages** from OpenClaw into SHIMS. |

## Why the channel bridge exists

There is no official API for a personal WhatsApp account. OpenClaw's WhatsApp
plugin already owns a real WhatsApp Web (baileys) session and broadcasts every
inbound message through the `message_received` plugin hook. Subscribing to that
hook is much less fragile than a second scraper, and it keeps exactly one
WhatsApp session on the machine.

The relay is **inbound only**. It observes; it never replies, rewrites, or
cancels delivery, and a SHIMS outage can never wedge your messaging — failures
are logged and dropped.

## Setup

### 1. SHIMS side

Set a strong shared secret in the SHIMS `.env` (the relay fails closed without
one — this endpoint ingests message content from another process):

```bash
SHIMS_BRIDGE_TOKEN=<32+ random chars>
```

Restart the SHIMS backend so `/api/channels/inbound` is registered, then check:

```bash
curl http://127.0.0.1:8010/api/channels/whatsapp/recent
```

### 2. OpenClaw side

These are edits to `~/.openclaw/openclaw.json`. **Back it up first** — this repo
does not modify that file for you.

Three things are required, and the WhatsApp channel is currently inert on this
machine because the first two are missing:

```jsonc
{
  "plugins": {
    "allow": [
      "lmstudio",
      "memory-core",
      "shims-enterprise-bridge",
      "whatsapp",              // (1) currently absent — the plugin is
                               //     installed and enabled but not allowed,
                               //     so it never loads
      "shims-channel-bridge"
    ],
    "load": {
      "paths": [
        "C:\\Users\\direc\\Documents\\Codex\\...\\openclaw-enterprise-bridge\\plugin",
        "C:\\d\\SHIMS\\integrations\\openclaw\\shims-channel-bridge"
      ]
    },
    "entries": {
      "whatsapp": {
        "enabled": true,
        "config": {
          "pluginHooks": {
            "messageReceived": true   // (2) without this the WhatsApp plugin
                                      //     never fires the hook at all
          }
        }
      },
      "shims-channel-bridge": {
        "enabled": true,
        "config": {
          "shimsUrl": "http://127.0.0.1:8010",
          "bridgeToken": "<same value as SHIMS_BRIDGE_TOKEN>",
          "channels": ["whatsapp"],
          "relayGroups": false
        }
      }
    }
  },
  "channels": {
    "whatsapp": {                     // (3) channels is currently {} — the
      "enabled": true,                //     channel must exist to be active
      "dmPolicy": "allowlist",
      "allowFrom": ["<your number>@s.whatsapp.net"]
    }
  }
}
```

On `dmPolicy`: an earlier config used `allowlist` with an empty `allowFrom`,
which silently **drops every DM** — that warning is still in
`00_SHIMS_DESKTOP_HUB/openclaw-dashboard-url.txt`. Either list the senders you
want relayed or change the policy deliberately.

### 3. Pair and verify

Restart the gateway, pair WhatsApp (QR scan — you must do this yourself; it is a
login), then send yourself a message and check:

```bash
curl http://127.0.0.1:8010/api/channels/whatsapp/recent
```

From inside OpenClaw, the `shims_channel_status` tool reports the same thing.

## Privacy

- Messages are stored locally in `data/state/shims_channels.sqlite3`.
- Retention is bounded (`MAX_RETAINED`, default 2000 per channel); bodies are
  truncated to 4000 chars.
- Group messages are **not** relayed unless you set `relayGroups: true`.
- Nothing is sent anywhere outside this machine.
