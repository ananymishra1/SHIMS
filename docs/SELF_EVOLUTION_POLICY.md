# Self-Evolution Policy

The self-evolution engine is intentionally guarded.

It can:

- Stage proposed changes.
- Apply changes only inside approved paths.
- Reject `.env`, database, certificate, key, and backup files.
- Create a timestamped backup before every applied change.
- Run Python compile validation.
- Roll back automatically if validation fails.

It should not:

- Edit secrets.
- Edit live databases.
- Run unrestricted shell commands.
- Merge changes into production without human approval.

Recommended production model: use Git branches, protected main branch, status checks, and manual approval.
