/**
 * SHIMS Channel Bridge — an OpenClaw plugin that relays inbound channel
 * messages into the SHIMS Omni backend.
 *
 * Why a plugin rather than SHIMS talking to WhatsApp directly: there is no
 * official API for a personal WhatsApp account. OpenClaw's WhatsApp plugin
 * already owns a real WhatsApp Web (baileys) session, and it broadcasts each
 * inbound message through the `message_received` plugin hook. Subscribing to
 * that hook is far less fragile than a second scraper, and it keeps exactly one
 * WhatsApp session on the machine.
 *
 * Prerequisites on the OpenClaw side (see integrations/openclaw/README.md):
 *   - "whatsapp" present in plugins.allow
 *   - plugins.entries.whatsapp.config.pluginHooks.messageReceived = true
 *     (without it the WhatsApp plugin never fires the hook)
 *   - a channels.whatsapp block so the channel is actually active
 *
 * This relay is inbound-only and never replies. It is an observer: it does not
 * cancel, rewrite, or delay delivery, and a SHIMS outage must not wedge the
 * user's messaging — so every failure is swallowed after logging.
 */

const DEFAULTS = {
  shimsUrl: "http://127.0.0.1:8010",
  channels: ["whatsapp"],
  relayGroups: false,
  timeoutMs: 8000,
};

function config(api) {
  const raw = api.pluginConfig ?? {};
  const channels = Array.isArray(raw.channels) && raw.channels.length
    ? raw.channels.map((c) => String(c).toLowerCase())
    : DEFAULTS.channels;
  return {
    shimsUrl: String(raw.shimsUrl || DEFAULTS.shimsUrl).replace(/\/$/, ""),
    bridgeToken: String(raw.bridgeToken || ""),
    channels,
    relayGroups: raw.relayGroups === true,
    timeoutMs: Math.min(Math.max(Number(raw.timeoutMs || DEFAULTS.timeoutMs), 1000), 30000),
  };
}

async function postJson(url, body, token, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-bridge-token": token,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`);
    }
    return text ? JSON.parse(text) : {};
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Normalize one hook event into the SHIMS /api/channels/inbound shape.
 *
 * Field names vary across OpenClaw versions and channels, so each value is
 * resolved from a small list of aliases rather than one hard-coded path — a
 * rename upstream should degrade one field, not drop the whole message.
 */
function toInbound(channel, event, ctx) {
  const pick = (...vals) => {
    for (const v of vals) {
      if (v !== undefined && v !== null && String(v) !== "") return String(v);
    }
    return "";
  };
  const messageId = pick(event.messageId, ctx?.messageId, event.id, event.key?.id);
  const threadId = pick(event.threadId, ctx?.threadId, event.chatId, event.sessionKey, ctx?.sessionKey);
  const senderId = pick(event.senderId, ctx?.senderId, event.from, event.sender);
  return {
    channel,
    message_id: messageId,
    body: pick(event.content, event.text, event.body),
    sender_id: senderId,
    sender_name: pick(event.senderName, event.pushName, event.senderDisplayName, senderId),
    thread_id: threadId,
    // Group chats are noisy and often not what the user wants mirrored into a
    // dashboard, so they are opt-in.
    is_group: Boolean(event.isGroup ?? event.group ?? String(threadId).endsWith("@g.us")),
    received_at: Number(event.timestamp || event.receivedAt || Date.now() / 1000) || undefined,
    metadata: {
      channel,
      runId: pick(ctx?.runId),
      replyToId: pick(event.replyToId, ctx?.replyToId),
    },
  };
}

export default {
  id: "shims-channel-bridge",
  name: "SHIMS Channel Bridge",
  description: "Relays inbound channel messages from OpenClaw into SHIMS Omni.",

  register(api) {
    const log = (msg, err) => {
      const line = `[shims-channel-bridge] ${msg}`;
      try {
        (api.logger?.warn ?? console.warn)(err ? `${line}: ${String(err.message || err)}` : line);
      } catch {
        /* logging must never throw inside a hook */
      }
    };

    api.on("message_received", async (event, ctx) => {
      const cfg = config(api);
      const channel = String(event?.channel ?? ctx?.channel ?? "").toLowerCase();

      if (!cfg.channels.includes(channel)) return;
      if (!cfg.bridgeToken) {
        log("bridgeToken is not configured — refusing to relay");
        return;
      }

      const payload = toInbound(channel, event ?? {}, ctx ?? {});
      // Without a stable id the receiver cannot dedupe, and WhatsApp redelivers.
      // Dropping is better than filling the feed with duplicates.
      if (!payload.message_id) return;
      if (payload.is_group && !cfg.relayGroups) return;
      if (!payload.body) return;

      try {
        await postJson(
          `${cfg.shimsUrl}/api/channels/inbound`,
          payload,
          cfg.bridgeToken,
          cfg.timeoutMs,
        );
      } catch (error) {
        // Observer semantics: SHIMS being down must never delay or break the
        // user's actual messaging, so this is logged and dropped.
        log(`relay failed for ${channel}/${payload.message_id}`, error);
      }
    }, { priority: 10, timeoutMs: 10000 });

    api.registerTool({
      name: "shims_channel_status",
      description: "Check whether the SHIMS channel relay is reachable and how many messages it has stored. Read-only.",
      parameters: {
        type: "object",
        properties: {
          channel: { type: "string", description: "Channel id (default whatsapp)" },
        },
        required: [],
        additionalProperties: false,
      },
      async execute(_id, params) {
        const cfg = config(api);
        const channel = String(params?.channel || "whatsapp").toLowerCase();
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
        try {
          const r = await fetch(`${cfg.shimsUrl}/api/channels/${channel}/recent?limit=1`, {
            signal: controller.signal,
          });
          const data = await r.json();
          const value = {
            ok: r.ok,
            channel,
            shimsUrl: cfg.shimsUrl,
            tokenConfigured: Boolean(cfg.bridgeToken),
            connected: Boolean(data?.connected),
            stored: Number(data?.count || 0),
            lastReceivedAt: data?.last_received_at ?? null,
          };
          return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }], details: value };
        } catch (error) {
          const value = { ok: false, channel, error: String(error.message || error) };
          return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }], details: value };
        } finally {
          clearTimeout(timer);
        }
      },
    });
  },
};
