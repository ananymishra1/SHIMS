# Architecture

SHIMS is split into two apps.

## Omni

Omni is the independent AI brain. It stores chat history in its own SQLite database and exposes chat, document, media, code, self-evolution, and enterprise bridge APIs.

## Enterprise

Enterprise is the factory execution layer. It stores users, experiments, COA templates, COA records, inventory, vendors, production batches, procurement requests, and audit logs.

## Bridge

Omni can call Enterprise only through the bridge endpoint and only when the configured bridge token matches.

## Upgrade path

The default local database is SQLite. For plant-level deployment, migrate to PostgreSQL, enable row-level security, configure backups, and place both apps behind HTTPS and a reverse proxy.
