# Verification — ReturnRelay 2.0

Executed on 24 November 2026 with Windows and Python 3.12.14; official MCP SDK 1.29.0. No original test files were deleted or changed.

## Baseline, before upgrade validation

The open workspace was C:\fscr; the named ReturnRelay_Working_Model folder was not present in the inspected locations. The linked repository was cloned to C:\fscr\mcpserver from commit bd60f3e20cd2db3ae44246bb89f145ed60629f40. A pristine source archive was saved as C:\fscr\returnrelay-original-bd60f3e.zip. No Git push was performed.

The initial baseline attempt could not import dependencies: Python was not on PATH; the created environment lacked tzdata/FastAPI. A bundled Python 3.12 environment and documented dependencies were then installed. Slow package downloads and competing installer locks required retrying; the successful installer completed.

Pristine baseline commands were then run against the unmodified archived source in C:\fscr\tmp\returnrelay-baseline, using the prepared environment:

- Core unittest suite: 12 cases, 8 errors. All errors were Windows WinError 32 during temporary SQLite database cleanup: connections were not closed by the original transaction context manager.
- Original MCP integration: real handshakes/discovery/calls and assertions reached the PASS line, but process exit failed during database cleanup with the same file lock. This is not counted as a passing baseline run.
- Original API integration: assertions reached the PASS line, but exited with the same database cleanup error. This is not counted as a passing baseline run.

The upgrade fixes the underlying connection lifetime in core.Connection.__exit__, retaining transactions and closing handles reliably on Windows.

## Final automated verification

Commands:

```powershell
.\scripts\run_tests.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.test_mcp
.\.venv\Scripts\python.exe -m tests.test_api
```

Latest full scripted run: **34 unittest cases passed**, plus the real stdio MCP integration and original API/download integration passed. The discover command also executes the original import-time API assertions; the script reruns the standalone API check explicitly. These integration scripts are additional checks, not extra unittest counts.

- Original 12 cases: deadline, occupied-slot avoidance, expiration, replacement-only, unclear policy, condition review, missing/expired/modified approval, duplicate replay, failure rollback, changed policy, inert receipt instructions.
- New 22 cases: authenticated ownership and CSRF, password/cookie properties, per-account empty state, database metrics, blocked decisions, receipt review, unsupported/invalid/oversize uploads, PDF extraction, missing evidence, draft persistence, lifecycle validation, new calendar conflict blocking, refund-document verification, cross-user files/plans/actions, real MCP errors, rate limiting, current evidence invalidating old plans, grounded assistant, invalid/unavailable optional-model fallback.
- MCP integration: three actual SDK subprocesses initialized, tools listed, evidence/calendar/actions called, approved outputs and duplicate retry checked, merchant notice not treated as bank settlement. Negotiated protocol: 2025-11-25.
- API integration: original request token, preview, safe failure, approval, EML/ICS download, retry and replacement-only stop.
- JavaScript: syntax checked with Node --check static/app.js.
- Python: compileall checked the application modules.
- Git diff --check: no whitespace errors; Git prints Windows line-ending notices only.

Dependencies emit a Pydantic settings forward-reference warning and Starlette's httpx test-client deprecation notice. These were visible and did not fail the executed tests. No warning was hidden to obtain a pass.

## Browser and startup verification

The actual application started via `.venv\Scripts\python.exe host.py` and served http://127.0.0.1:8000 . Browser checks used the running application, not mocked HTML:

- Created an account; empty dashboard loaded and session survived host restart.
- Imported fictional fixtures from Settings; metrics changed from zero to stored purchase counts.
- All ten navigation pages loaded.
- Completed all eleven eligible-return workflow steps with receipt, policy, eligibility, free slot, exact draft and approval.
- Induced action failure through the actual MCP tool, retried successfully, then observed existing-pair replay with no duplicates.
- MCP Activity showed three responsive connections, real protocol, discovered schemas and actual calls.
- Assistant answered the deadline from RR-1042 and POL-A-v1 in deterministic mode.
- Uploaded the fictional text PDF, reviewed extracted order/merchant/item/dates/amount/currency/payment reference, entered missing support email, confirmed fields and observed the new purchase persisted. This browser exercise exposed and fixed an undefined receipt variable in the upload callback.
- Viewed the responsive layout and opened the mobile navigation drawer. Dashboard screenshots saved under docs/screenshots.

Browser testing did not exhaustively click every lifecycle permutation or optional-provider failure; those have automated API/domain coverage. Live Tesseract recognition, a live Ollama model, Gmail/Calendar OAuth, merchant APIs and independent bank connectivity were not available or claimed tested. The default no-key path is verified.

## Scope and limitations

The five README scenarios are supported with fictional data and explicit human records. Verification is evidence of local application behavior, not production security certification, merchant acceptance, a real pickup or actual bank settlement. Optional provider contracts exist; live OAuth implementations are not delivered. See README for exact supported policy semantics and operating limits.
