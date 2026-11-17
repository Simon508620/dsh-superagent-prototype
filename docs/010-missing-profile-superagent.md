# 010 Missing profile superagent

This document explains the `[dsh-bug] missing-profile:superagent` signature the DSH bug reporter files, why it recurred after an earlier fix, and the deployment steps the operator asked for. It builds on [006 Deployment](006-deployment.md) and [009 Model listing 401](009-model-listing-401.md).

## Root cause

The log line comes from DSH's launcher, not from dsh-superagent itself. The Superagent desktop app starts its backend with:

```
node .../@deepseek-ai/dsh/lib/bin.js --profile superagent --patch runtime.patch.yml --no-open
```

DSH's `loadProfile` resolves `<home>/profiles/superagent` and throws `profile "superagent" does not exist` only when that directory has no `package.json`. Two independent facts make this signature misleading on the operator's machine:

1. **It was a real error once, from a different code path.** The earlier fix (PR #14) guarded `scripts/dsh-superagent` — the tmux service script — by checking the profile manifest before spawning `dsh --profile superagent`. But the desktop app launches the same DSH CLI directly, bypassing that script, so the guard never covered this launcher.

2. **The current reports are stale replays, not a live failure.** `backend.log` is append-only. The bug reporter tails it by byte offset, and the two lines it reported on 2026-11-17 were written on 2026-11-11, before the profile existed. When the reporter's state file was reset, those old bytes were scanned again and re-filed. On this machine `~/.dsh/profiles/superagent/package.json` now exists (with the bundle linked), so DSH can boot the profile again; there is no missing profile to fix.

So the condition the signature names is **absent now** — re-running the profile-creation command would be wrong, because it replaces the existing profile and deletes its `cordis.patch.yml` (see [006 Deployment](006-deployment.md), Install).

## Fix

The reporter's `Get-Signature` now checks the profile manifest before reporting a `missing-profile:<name>` line. It resolves the manifest from `$env:DSH_HOME` (default `~/.dsh`) at the moment the line is scanned:

- If `<home>/profiles/<name>/package.json` exists, the line is stale and is ignored — a replay of an error whose condition is gone.
- If it does not exist, the signature is still reported with high severity, so a genuinely missing profile is not hidden.

This keeps the signature honest for both cases without touching DSH's launcher or the service script. `tests/bug-reporter.spec.ps1` covers both branches by extracting the real `Get-Signature` function from the script and exercising it against a fake Harness home.

## Local deployment

The scheduled task runs the unpacked release copy, `C:\Users\Topgun620\bin\dsh-bug-report.ps1`, not the repository copy. The fix takes effect only after that copy is updated:

1. Replace `C:\Users\Topgun620\bin\dsh-bug-report.ps1` with this repository's `scripts/dsh-bug-report.ps1` (keep the UTF-8 BOM — Windows PowerShell 5.1 needs it for the Chinese comments).
2. Run it once in dry-run to confirm the stale line is ignored:
   `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\Topgun620\bin\dsh-bug-report.ps1 -DryRun`
   The output must no longer list `missing-profile:superagent` under "Detected signatures".
3. No backend restart is needed: the reporter is a scheduled task, and the next run reads the updated file.

Refreshing the whole release directory from the merged tree also picks up the fix; see [009 Model listing 401](009-model-listing-401.md), Updating the reporter. Closing issue #49 is a bookkeeping step separate from the deployment — the reporter will not re-file the signature once the updated script is in place.

## What to do about issue #49

Nothing in the profile needs changing. Verify `~/.dsh/profiles/superagent/package.json` exists (it does on this machine), deploy the updated reporter above, and close the issue as a stale/replayed report. Do **not** run `dsh superagent --from-default-profile web --dump-config` on this machine: that command is for a new install and would delete the profile's machine configuration.
