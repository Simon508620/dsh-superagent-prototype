# Hotfix · DSH startup failed: `1 required plugin did not activate` (issues #24 / #37 / #38 / #57)

> Local hotfix for the installed DSH credentials document, plus a reporter fix that stops the same dead failure being re-filed. It is **not** an upstream `@deepseek-ai/dsh-credentials-local` source change.

## Root cause (verified against this machine's logs)

`~/.dsh/profiles/superagent` loads `credentials` as a **required** plugin from `@deepseek-ai/dsh-credentials-local`. Its `loadInitial()` calls `parseCredentialsDocument()`, which uses `yaml.parseDocument()`. On 11-14 the document at `~/.dsh/.credentials.yaml` had a JSON-style object block spliced into its YAML `refs:` mapping:

```yaml
version: 1
refs:
  ANYSEARCH_API_KEY: as_sk_...
  {                                  # <-- bare '{' after a mapping key
    SENSENOVA1_API_KEY: sk-...,
    MAGPIE_GATEWAY_KEY: magpie,
  }
records: ...
```

YAML reads the bare `{` as a **multiline implicit key** and rejects the file with `MULTILINE_IMPLICIT_KEY / MISSING_CHAR / IMPOSSIBLE at line 4`. Because `credentials` is required, DSH aborts startup:

```
dsh: startup failed: 1 required plugin did not activate
Failed plugins (1): credentials  Package: @deepseek-ai/dsh-credentials-local
Plugins waiting for services (18): connection (required), authorization, ...
```

The full failure is in `backend.log` lines 515–548 and the three `startup-2026-11-14T08-0*.log` snapshots.

### Why it was reported *again* on 11-17

The document was already repaired on 11-14 (current `.credentials.yaml` parses cleanly with DSH's own parser: 4 refs, 1 record). The 11-17 report is a **bug in the reporter**, not a new startup failure:

- **The decisive defect:** the reporter read its state file with `Get-Content -Raw` (no `-Encoding`). On this Chinese-locale Windows box PS 5.1 decodes UTF-8 as GBK, mangling the Chinese characters in signature keys (e.g. the 灵枢 bridge sig). `ConvertFrom-Json` then throws, the `catch` silently resets `$state = @{}`, **all byte offsets are lost**, and every signature in the whole log is re-detected and re-filed every 10 minutes. That is why `startup-failed-required-plugin` was filed as #38, #56, #82, #122 … and `credentials-invalid-yaml` as #37, #57, #83 … Despite the fix's other changes, a dry run still reported 15 signatures (count=3 for this one) until this was corrected; after the UTF-8 read fix the same dry run reports **0 signatures**.
- A second defect incremented `dayCount` on **every detection**, not every distinct day, so every repeat looked like a persistent recurrence and re-armed inside the cooldown.
- A third defect scanned immutable `startup-*.log` snapshots with no lookback, so the three 11-14 snapshots re-exposed old errors on first scan.

Recent `backend.log` starts (11-15 → 11-17) all show a normal `dsh web:` line and no startup failure; the current `.credentials.yaml` passes DSH's parser.

## Fix

1. **Reporter** (`scripts/dsh-bug-report.ps1`, both this repo and the live `C:\Users\Topgun620\bin` copy):
   - Read the state file as **UTF-8** (`[IO.File]::ReadAllText(..., UTF8)`) so `ConvertFrom-Json` succeeds and offsets survive across runs. This is the change that stops the re-flood.
   - Count `dayCount` per **distinct local day** so a same-day repeat no longer re-arms the cooldown.
   - Scan only `startup-*.log` snapshots written within a 24 h lookback window. Append-only `app.log`/`backend.log` still cover older failures.
2. **Hotfix** (`hotfix/startup-credentials-yaml/`): a surgical, idempotent repair for the one document shape above. It unwraps the JSON block into ordinary `  KEY: value` lines, preserves every key/value and every other byte, backs up the original before an atomic rename, and refuses to guess on any other document shape.

## Deployment steps (this machine)

The document itself is already valid; step 1 is a safety check. The reporter fix is what stops the false alarm and is already applied to the live copy.

```bash
# 1) Verify the credentials document parses with DSH's own parser
cd <checkout>
node hotfix/startup-credentials-yaml/verify.mjs

# 2) If (and only if) verify reports BAD/repairable, repair it:
node hotfix/startup-credentials-yaml/apply.mjs
node hotfix/startup-credentials-yaml/verify.mjs

# 3) Reporter: this repo's scripts/dsh-bug-report.ps1 is authoritative.
#    Refresh the copy the scheduled task runs so the fix takes effect:
copy /Y scripts\dsh-bug-report.ps1 C:\Users\Topgun620\bin\dsh-bug-report.ps1

# 4) No restart is needed for the reporter; the next 10-minute scheduled run
#    uses the new script. To pick up a repaired credentials document, restart
#    the DSH profile so @deepseek-ai/dsh-credentials-local reloads it:
scripts\dsh-superagent --profile superagent --respect-stop restart
```

**Important:** the installed plugin is not patched. This hotfix only touches the credentials document. If a future DSH upgrade rewrites `~/.dsh/.credentials.yaml` into the same broken shape, rerun `apply.mjs`.

## Verification performed

- `repair()` applied to the real `.credentials.yaml.bak-20261114-corrupt` produced a document that `parseCredentialsDocument()` accepts (4 refs, 1 record, all keys preserved).
- `apply.mjs` on a temp corrupt copy: `REPAIRED`, original moved to a timestamped `.bak-`, secrets byte-preserved; a second run is a no-op (`already OK`).
- `verify.mjs`: corrupt → `BAD (repairable)`, repaired → `OK`; parser detection is optional so the script works on machines without DSH installed at the path checked.
- Reporter: PowerShell parser reports no syntax errors. Dry run before the UTF-8 fix reported 15 signatures (including `startup-failed-required-plugin` count=3); after the fix the same dry run reports **0 signatures**, confirming stale failures are no longer re-filed.
