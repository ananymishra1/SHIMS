# SHIMS Mailbox & Capture Plan

The shared `bluetooth_content_share.html` file contains a Google Collections/Saved link. SHIMS should treat that pattern as a universal capture flow: links, snippets, RFQs, invoice notes, Gmail metadata, and campaign ideas enter one inbox, then become RAG context and follow-up tasks.

## Implemented Surface

- `POST /capture/share` stores a link/note/snippet, ingests it into Omni Brain RAG, and queues a review task.
- `GET /capture/items` lists recent captures.
- `GET /mailbox/status` reports local mailbox, capture, Gmail configuration, and policy state.
- `GET /mailbox/oauth/start` builds a Google OAuth URL only when `SHIMS_GMAIL_CLIENT_ID` is configured.
- `POST /mailbox/import` imports a user-provided mail item into local memory/RAG.
- `POST /mailbox/gmail/sync` syncs Gmail metadata only when an OAuth access token is explicitly configured or supplied.
- Enterprise exposes `/mailbox` and matching `/api/mailbox/*` / `/api/capture/*` endpoints.

## Gmail / Play Store Rules

Gmail cannot be accessed silently. SHIMS must use user-visible OAuth consent, minimal scopes, and a clear privacy disclosure.

Official references:

- Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail API Services User Data Policy: https://developers.google.com/gmail/api/policy
- Google API Services User Data Policy: https://developers.google.com/terms/api-services-user-data-policy
- Google Play permissions and sensitive API policy: https://support.google.com/googleplay/android-developer/answer/9888170
- Play Console Data safety form help: https://support.google.com/googleplay/android-developer/answer/10787469

## Production Checklist

1. Keep shared-link capture available without Gmail permissions.
2. Use Gmail OAuth only after the user chooses mailbox sync.
3. Default to `https://www.googleapis.com/auth/gmail.metadata` for headers/snippets where possible.
4. Use broader Gmail scopes only when a feature truly needs body access or sending.
5. Encrypt OAuth tokens at rest in production; never commit them.
6. Add Data Safety disclosure for email metadata/content, purpose, optionality, deletion, and user control.
7. Require confirmation before any external email send, campaign send, payment action, or regulated enterprise approval.
