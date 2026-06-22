# AGENTS.md

For substantial questions about this project, use Graphify first for orientation, then verify against the local repository and live VPS/router state.

Project boundaries:

- Do not commit backend secrets, device tokens, WireGuard keys, TLS private keys, or SQLite databases.
- Keep public install scripts secret-free.
- New devices must register as `pending`; backend must not issue tasks until admin approval.
- Agent actions must remain allowlisted. Do not add arbitrary remote shell execution.
- Prefer reversible, non-disruptive router changes. WAN, DNS, firewall, and default route changes require explicit task design and verification.

