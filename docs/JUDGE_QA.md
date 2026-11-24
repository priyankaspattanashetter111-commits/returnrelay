# Judge Q&A — ReturnRelay 2.0

**Is MCP real?** Yes. The host starts three separate official Python SDK server processes, initializes each, discovers schemas and uses tools/call over stdio. MCP Activity performs real pings and displays persisted redacted calls. Direct APIs could implement the same domain logic; MCP supplies discoverable, replaceable service boundaries.

**Where is AI?** The usable baseline is deterministic extraction, policy decisions and grounded question answering. Optional Ollama provides schema-validated local explanations. Rules remain authoritative, and invalid or unavailable model output falls back. No LLM is installed or connected by default.

**Can an assistant approve a return?** No. Only an authenticated human approval request causes the host to sign the exact stored plan. The Actions MCP process verifies HMAC, expiry, current receipt hash, policy and eligibility. Read-only MCP processes do not receive the signing key.

**What was preserved?** All original source roles, fixtures and tests, the legacy fictional API, exact approval mechanism, atomic local pair creation, duplicate protection and EML/ICS downloads. Additive SQLite migration preserves old plans and actions.

**Are statistics real?** They are computed from account-scoped SQLite purchases and requests. Fictional examples are explicitly identified; there are no marketing recovery numbers or fabricated user counts.

**What happens on failure?** Simulated operation failure runs inside the actual action tool and commits neither output. Retry creates or replays one pair. An unavailable process returns failure, not success; restart the host to reconnect. External providers would require durable outbox/reconciliation rather than claiming distributed atomicity.

**How are receipts trusted?** Uploads are private, validated and unconfirmed. Regex extraction never invents missing fields. Users review and confirm against originals. These are user-confirmed records, not independent merchant authentication. Image OCR requires a separate Tesseract installation.

**What does bank verified mean?** An authenticated user uploaded evidence, provided the exact matching amount/reference and explicitly confirmed reviewing a bank credit. It is not independent bank verification. A merchant notice cannot set this flag automatically. Partial mismatches require review.

**Does it send email or book a pickup?** No. It creates actual downloadable email drafts and calendar reminders. Submission is a separately recorded user action. Gmail, Google Calendar, merchant and bank APIs are not connected.

**What policy types work?** Explicit delivery-plus-calendar-days terms at 23:59 IST, unused items with original packaging, exclusion categories and replacement-only policies. Unknown windows, exceptions and conflicting terms require human review. Real policy quotations are supplied by users; the app does not fabricate merchant rules.

**Is it production secure?** It adds password hashing, private sessions, CSRF, ownership checks, bounded uploads, rate limits and HMAC approvals. It is designed for localhost on a trusted machine, not audited public hosting. Files are not encrypted by the app, the local database owner can modify records, and account recovery and cloud OAuth are not delivered.

**What evidence demonstrates quality?** See TEST_RESULTS.md for actual automated runs and browser coverage, including original tests, new multi-user/security flows, real MCP discovery/calls, extraction, failure/retry and refund evidence.
