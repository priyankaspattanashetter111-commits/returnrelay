# ReturnRelay 2.0 technical blueprint

The existing FastAPI host remains an actual client of three official-SDK MCP subprocesses. The evidence role reads account purchases, receipts, versioned policies and refund evidence. Calendar reads recorded occupancy, suggests preparation slots and validates conflicts/deadlines. Actions validates host-issued exact-plan authorization, prepares local outputs and reports idempotent status.

The browser uses /api/v2 authenticated routes. A random HttpOnly SameSite session is persisted as a hash. Each session has its own CSRF token. The host supplies user identity to tools; browser input cannot choose another user scope. Legacy routes expose only original fictional unowned data.

storage.migrate adds tables and indexes without replacing legacy plans, actions or audit. Each database context now closes the SQLite connection after commit/rollback, fixing the Windows file locks found during baseline testing. Evidence metadata is account-owned. Files use random names and authenticated attachment downloads; static assets have no upload access.

The rule engine explains policy/version, source IDs, deadline and missing information. It stops on insufficient, ambiguous, conflicting, excluded, replacement-only or expired cases. Explicit supported policy semantics and the real-versus-fictional clock are documented in README. Saved drafts and review cases remain visible in tracking.

Previewed plans include owner, condition, reason, receipt hash, policy snapshot, exact draft, slot, effects and expiry. Human approval binds the whole stored plan by HMAC. Host rechecks calendar conflict, Actions checks policy/current evidence/eligibility, and BEGIN IMMEDIATE plus a unique plan ID commits one local pair. The adapter renders RFC 822 EML and timezone-aware UTC ICS. This local transaction does not claim cross-provider atomicity.

The assistant uses purchase and refund records fetched through MCP, offers deterministic source-linked answers, and optionally requests a strictly validated local Ollama explanation. Model output is never authorization. No cloud AI provider or OAuth provider is connected by default.

MCP Activity separates initialized connections, current ping responsiveness, tool outcome and business outcome. Calls retain measured duration, actual parameters with token redaction, and actual response structure with personal fields redacted. Account history is persisted in mcp_events. A stopped subprocess requires restarting the host; failure is never converted into successful business output.

Refund evidence can record requested, merchant processing, merchant-confirms-processed, pending bank verification and user-verified credit. Only explicit document/amount/reference confirmation sets verified. Timelines never infer user submission from draft creation.

The default deployment is one localhost worker on a trusted machine. Remaining boundaries include OS-readable SQLite/files, no account recovery, no malware scanning, in-memory rate limiting, narrow supported policy semantics and manual external submission. See README for full setup, endpoint/schema reference, optional configuration and limitations.

Editable architecture and workflow sources are in architecture.dot, workflow.dot and architecture.mmd. Tests and actual validation records are in TEST_RESULTS.md.
