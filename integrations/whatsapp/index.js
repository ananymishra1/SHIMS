/**
 * SHIMS WhatsApp sidecar — direct baileys integration.
 *
 * Replaces the OpenClaw relay: SHIMS owns the WhatsApp Web session itself,
 * so OpenClaw doesn't need to be running. Messages are posted to the SHIMS
 * channels inbound endpoint, same store the hub dashboard reads.
 *
 * Endpoints:
 *   GET  /status  — connection state, phone number, uptime
 *   GET  /qr      — QR code as PNG (for initial pairing)
 *   GET  /qr/text — QR code as terminal text
 *   POST /logout  — disconnect and clear session
 *
 * Environment / config:
 *   SHIMS_WHATSAPP_PORT     — sidecar port (default 5116)
 *   SHIMS_BRIDGE_TOKEN      — shared secret for the inbound endpoint
 *   SHIMS_OMNI_PORT         — SHIMS backend port (default 8010)
 *   SHIMS_WHATSAPP_GROUPS   — "true" to relay group messages (default false)
 */

import { createServer } from "http";
import { readFileSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } from "@whiskeysockets/baileys";
import QRCode from "qrcode";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

function loadEnv() {
  const envPaths = [
    join(__dirname, "..", "..", ".env"),
    join(__dirname, ".env"),
  ];
  for (const p of envPaths) {
    if (!existsSync(p)) continue;
    for (const line of readFileSync(p, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq < 1) continue;
      const key = trimmed.slice(0, eq).trim();
      const val = trimmed.slice(eq + 1).trim();
      if (!process.env[key]) process.env[key] = val;
    }
  }
}
loadEnv();

const PORT = Number(process.env.SHIMS_WHATSAPP_PORT || 5116);
const SHIMS_URL = `http://127.0.0.1:${process.env.SHIMS_OMNI_PORT || 8010}`;
const BRIDGE_TOKEN = (process.env.SHIMS_BRIDGE_TOKEN || "").trim();
const RELAY_GROUPS = process.env.SHIMS_WHATSAPP_GROUPS === "true";
const AUTH_DIR = join(__dirname, ".auth");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sock = null;
let currentQr = null;
let connectionState = "disconnected";
let phoneNumber = "";
let startedAt = Date.now();
let messageCount = 0;
let lastError = "";
let reconnectTimer = null;

// ---------------------------------------------------------------------------
// Relay to SHIMS
// ---------------------------------------------------------------------------

async function relayToShims(payload) {
  if (!BRIDGE_TOKEN) {
    console.warn("[whatsapp] SHIMS_BRIDGE_TOKEN not set — message dropped");
    return;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const resp = await fetch(`${SHIMS_URL}/api/channels/inbound`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-bridge-token": BRIDGE_TOKEN,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      console.warn(`[whatsapp] relay failed: HTTP ${resp.status} ${text.slice(0, 200)}`);
    }
  } catch (err) {
    console.warn(`[whatsapp] relay error: ${err.message}`);
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// baileys connection
// ---------------------------------------------------------------------------

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: true,
    browser: ["SHIMS", "Desktop", "1.0.0"],
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      currentQr = qr;
      connectionState = "qr_pending";
      console.log("[whatsapp] QR code ready — scan at http://127.0.0.1:" + PORT + "/qr");
    }

    if (connection === "open") {
      currentQr = null;
      connectionState = "connected";
      phoneNumber = sock.user?.id?.split(":")[0] || sock.user?.id || "";
      startedAt = Date.now();
      lastError = "";
      console.log(`[whatsapp] connected as ${phoneNumber}`);
    }

    if (connection === "close") {
      connectionState = "disconnected";
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const reason = lastDisconnect?.error?.message || `status ${statusCode}`;
      lastError = reason;
      console.log(`[whatsapp] disconnected: ${reason}`);

      if (statusCode === DisconnectReason.loggedOut) {
        console.log("[whatsapp] logged out — clear .auth and restart to re-pair");
        connectionState = "logged_out";
      } else {
        const delay = Math.min(30000, 5000 + Math.random() * 5000);
        console.log(`[whatsapp] reconnecting in ${Math.round(delay / 1000)}s`);
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(startSocket, delay);
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      if (!msg.message) continue;

      const jid = msg.key.remoteJid || "";
      const isGroup = jid.endsWith("@g.us");
      if (isGroup && !RELAY_GROUPS) continue;
      const fromMe = Boolean(msg.key.fromMe);

      const text =
        msg.message.conversation ||
        msg.message.extendedTextMessage?.text ||
        msg.message.imageMessage?.caption ||
        msg.message.videoMessage?.caption ||
        msg.message.documentMessage?.caption ||
        "";
      if (!text) continue;

      const senderId = isGroup
        ? (msg.key.participant || "")
        : jid;

      const payload = {
        channel: "whatsapp",
        message_id: msg.key.id || "",
        body: text,
        // For outbound (fromMe) messages the counterparty is the chat jid —
        // that is who WE quoted. Sender becomes "You".
        sender_id: senderId.replace("@s.whatsapp.net", ""),
        sender_name: fromMe ? "You" : (msg.pushName || senderId.split("@")[0]),
        thread_id: jid,
        is_group: isGroup,
        received_at: msg.messageTimestamp
          ? Number(msg.messageTimestamp)
          : Date.now() / 1000,
        metadata: {
          channel: "whatsapp",
          type: Object.keys(msg.message || {})[0] || "text",
          is_mine: fromMe,
        },
      };

      messageCount++;
      await relayToShims(payload);
    }
  });
}

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = url.pathname;

  res.setHeader("Access-Control-Allow-Origin", "*");

  if (req.method === "GET" && path === "/status") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({
      ok: true,
      state: connectionState,
      phone: phoneNumber,
      relay_groups: RELAY_GROUPS,
      messages_relayed: messageCount,
      uptime_s: Math.round((Date.now() - startedAt) / 1000),
      bridge_token_set: Boolean(BRIDGE_TOKEN),
      shims_url: SHIMS_URL,
      last_error: lastError,
    }));
    return;
  }

  if (req.method === "GET" && path === "/qr") {
    if (!currentQr) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({
        ok: true,
        state: connectionState,
        message: connectionState === "connected"
          ? "Already connected — no QR needed."
          : "No QR code available. Restart the sidecar if you need to re-pair.",
      }));
      return;
    }
    try {
      const png = await QRCode.toBuffer(currentQr, { width: 300, margin: 2 });
      res.writeHead(200, { "content-type": "image/png" });
      res.end(png);
    } catch (err) {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  if (req.method === "GET" && path === "/qr/text") {
    if (!currentQr) {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end(connectionState === "connected"
        ? "Already connected."
        : "No QR code available.");
      return;
    }
    try {
      const text = await QRCode.toString(currentQr, { type: "terminal", small: true });
      res.writeHead(200, { "content-type": "text/plain" });
      res.end(text);
    } catch (err) {
      res.writeHead(500, { "content-type": "text/plain" });
      res.end(err.message);
    }
    return;
  }

  if (req.method === "POST" && path === "/logout") {
    try {
      if (sock) await sock.logout();
    } catch {}
    connectionState = "logged_out";
    currentQr = null;
    phoneNumber = "";
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, message: "Logged out. Delete .auth/ and restart to re-pair." }));
    return;
  }

  res.writeHead(404, { "content-type": "application/json" });
  res.end(JSON.stringify({ error: "not found" }));
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[whatsapp] sidecar listening on http://127.0.0.1:${PORT}`);
  console.log(`[whatsapp] relaying to ${SHIMS_URL}/api/channels/inbound`);
  if (!BRIDGE_TOKEN) {
    console.warn("[whatsapp] WARNING: SHIMS_BRIDGE_TOKEN is not set — messages will be dropped");
  }
  startSocket();
});
