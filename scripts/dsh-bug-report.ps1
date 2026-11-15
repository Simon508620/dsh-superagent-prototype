# dsh-bug-report.ps1
# Auto-detect DSH runtime bugs from Superagent logs and file issues for Superagent to self-fix.
# Flow: read new bytes from app.log + backend.log (append-only tail) -> parse error signatures ->
#       dedupe against open issues with label dsh-bug -> create issue (as operator) @mention agent.
# State: bug-report-state.json under state dir (offsets per log + seen signatures + last counts).
# Usage:
#   dry-run : pwsh -File dsh-bug-report.ps1 -DryRun
#   live    : pwsh -File dsh-bug-report.ps1
#   scheduled (recommended): powershell.exe -NoProfile -ExecutionPolicy Bypass -File <this>  every 10 min

param(
    [string]$Repo = "Simon508620/dsh-superagent-prototype",
    [string]$AgentMention = "@Simon508620",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$appLog  = "C:\Users\Topgun620\AppData\Roaming\Superagent\app.log"
$backLog = "C:\Users\Topgun620\AppData\Roaming\Superagent\backend.log"
$stateDir = "C:\Users\Topgun620\.dsh\state\superagent"
$stateFile = Join-Path $stateDir "bug-report-state.json"
$label = "dsh-bug"
$MaxNewGeneric = 5   # cap: at most this many brand-new generic signatures per run (anti-flood)
$CooldownHours = 48        # cooldown: after an issue is created for a signature, re-suppress it for this long
$PersistentThreshold = 2   # same local day seen >= this many times = persistent recurrence -> re-arm even in cooldown

# --- state (offsets per log, seen signature -> last-seen/count) ---
# NOTE: PS 5.1 has no ConvertFrom-Json -AsHashtable (PS7-only). Load as PSCustomObject
# then rebuild a hashtable so $state["offsets"][$key] and $state.ContainsKey work.
$state = @{}
if (Test-Path $stateFile) {
    try {
        $raw = Get-Content $stateFile -Raw | ConvertFrom-Json
        if ($raw) {
            $state = @{}
            foreach ($p in $raw.PSObject.Properties) {
                if ($p.Name -eq "offsets") {
                    $h = @{}
                    foreach ($pp in $p.Value.PSObject.Properties) { $h[$pp.Name] = $pp.Value }
                    $state["offsets"] = $h
                } elseif ($p.Name -eq "seen") {
                    $h = @{}
                    foreach ($pp in $p.Value.PSObject.Properties) {
                        $e = @{}
                        foreach ($q in $pp.Value.PSObject.Properties) { $e[$q.Name] = $q.Value }
                        $h[$pp.Name] = $e
                    }
                    $state["seen"] = $h
                } elseif ($p.Name -eq "armed") {
                    $h = @{}
                    foreach ($pp in $p.Value.PSObject.Properties) { $h[$pp.Name] = $pp.Value }
                    $state["armed"] = $h
                } else {
                    $state[$p.Name] = $p.Value
                }
            }
        }
    } catch { $state = @{} }
}
if (-not $state.ContainsKey("offsets")) { $state["offsets"] = @{} }
if (-not $state.ContainsKey("seen"))    { $state["seen"]    = @{} }
if (-not $state.ContainsKey("armed"))   { $state["armed"]   = @{} }

# --- tail reader: returns (newText, newOffset) ---
function Read-Tail([string]$path, [long]$lastOffset) {
    if (-not (Test-Path $path)) { return @{ text = ""; offset = $lastOffset } }
    $fi = Get-Item $path
    $len = $fi.Length
    if ($lastOffset -gt $len) { $lastOffset = 0 }  # log rotated/truncated
    if ($len -eq $lastOffset) { return @{ text = ""; offset = $lastOffset } }
    $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $fs.Seek($lastOffset, 'Begin') | Out-Null
        $buf = New-Object byte[] ($len - $lastOffset)
        $read = $fs.Read($buf, 0, $buf.Length)
        if ($read -gt 0) {
            # decode as UTF-8 (logs are UTF-8; drop BOM if present)
            $txt = [System.Text.Encoding]::UTF8.GetString($buf, 0, $read)
            if ($txt.StartsWith([char]0xFEFF)) { $txt = $txt.Substring(1) }
            return @{ text = $txt; offset = $len }
        }
        return @{ text = ""; offset = $len }
    } finally { $fs.Close() }
}

# --- noise filter: lines that never represent a bug (UI/HTML payloads, routine polling, success logs) ---
$NoisePatterns = @(
    'did-finish-load',
    '^\s*$',
    '^\[.*\] \[ui\]',
    '0 notifications',
    'next poll',
    'polling',
    'WebSocket',
    'heartbeat',
    'health check',
    'version check',
    'update check',
    'shutting down',
    'graceful',
    'GET /',
    'POST /',
    '\b200\b',
    '\b304\b',
    '\[info\]',
    '\[debug\]',
    '\[trace\]',
    '\[notice\]',
    '\binfo\b',
    '\bdebug\b',
    'QUOTA:|tpm/rpm|inference exceeds',
    'bsk probe failed|spawn bsk ENOENT'
)

# --- generic error markers: strong = reliable error signal even without a log tag; weak = needs a known tag ---
$StrongErrorPattern = 'Error:|\[error\]|Unhandled|Exception|TypeError|ReferenceError|SyntaxError|RangeError|ELOCKED|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOENT|EPERM|EACCES|ESYNC'
$WeakErrorPattern   = ' ERROR |failed:|\[fatal\]|FATAL'

$GenericHighPattern = 'Unhandled|TypeError|ReferenceError|SyntaxError|RangeError|ELOCKED|ECONNREFUSED|fatal|FATAL|ENOENT'

# --- signature extraction (specific signatures keep stable names; generic adds a normalized fallback) ---
function Get-Signature([string]$line) {
    # 1. specific, well-understood signatures (names must stay stable for dedupe across runs).
    #    NOTE: stale-lock is NOT suppressed here. Re-arm logic (cooldown + persistent
    #    recurrence) lives in the main loop; the old permanent suppression (issue #19 closed
    #    on 11-12) caused the 11-14 orphan-lock recurrence to be missed entirely.
    if ($line -match "Lock file is already being held") {
        return @{ sig = "stale-lock"; sev = "high"; title = "Stale lock file blocks plugin activation (proper-lockfile)" }
    }
    if ($line -match 'bsk probe failed|spawn bsk ENOENT') { return $null }
    if ($line -match 'profile "([^"]+)" does not exist') {
        $p = $Matches[1]
        return @{ sig = "missing-profile:$p"; sev = "high"; title = "Profile `"$p`" does not exist at startup" }
    }
    if ($line -match "memory subagent stopped with max-tokens") {
        return @{ sig = "mnemon-idle-review-max-tokens"; sev = "medium"; title = "Mnemon idle review failed: memory subagent hit max-tokens" }
    }
    # DSH DeepSeek listing transport sends the API key as x-api-key on GET /models; the
    # platform answers 401 and the plugin falls back to its bundled catalog (issue #23).
    # The upstream fix ships in a @deepseek-ai/dsh-llm-deepseek update, so this warning is
    # only meaningful when it persists after upgrading DSH: keep it visible, never silent.
    if ($line -match "model listing from .*failed .*HTTP \d+\).*using the bundled catalog") {
        return @{ sig = "llm-deepseek-model-listing-401"; sev = "medium"; title = "DeepSeek model listing answered HTTP 401; bundled catalog fallback" }
    }

    # 2. noise filter
    foreach ($np in $NoisePatterns) { if ($line -match $np) { return $null } }

    # 3. extract log tag: [ts][pid][tag] msg  (tag empty/unknown -> not attributable, skip unless strong error)
    $tag = $null
    $core = $line
    if ($line -match '^\[[^\]]+\]\[[^\]]+\]\[([^\]]+)\]\s+(.*)$') {
        $tag = $Matches[1]
        $core = $Matches[2]
    }
    if ($tag -and ($tag -eq "ui" -or $tag -eq "info" -or $tag -eq "debug" -or $tag -eq "trace" -or $tag -eq "notice")) { return $null }

    # 4. generic error detection
    $isStrong = ($line -match $StrongErrorPattern)
    $isWeak   = ($line -match $WeakErrorPattern)
    if (-not $isStrong -and -not $isWeak) { return $null }
    if (-not $isStrong -and -not $tag) { return $null }        # weak signal needs an attributable tag

    # 5. normalize the error core into a stable, dedupe-able key
    $core = $core -replace 'https?://\S+', '<url>'
    $core = $core -replace '[A-Za-z]:\\[^\s]+', '<path>'
    $core = $core -replace '[\w-]{8,}-[\w-]{4,}-[\w-]{4,}-[\w-]{4,}-[\w-]{12}', '<guid>'
    $core = $core -replace '\d+', '<n>'
    $core = $core -replace '\s+', ' '
    $core = $core.Trim()
    # PS 5.1 mangles native args containing double quotes; keep sigs quoteless
    $core = $core -replace '"', "'"
    $core = $core -replace '[\r\n\t]', ' '
    $core = $core -replace '\s+', ' '
    $core = $core.Trim()
    if ([string]::IsNullOrWhiteSpace($core)) { return $null }
    if ($core.Length -gt 120) { $core = $core.Substring(0, 120) }

    $sig = "generic:$($tag):$core"
    $sev = "medium"
    if ($line -match $GenericHighPattern) { $sev = "high" }
    return @{ sig = $sig; sev = $sev; title = "[$tag] $core" }
}

# --- dedupe: open issues with label dsh-bug on repo -> set of signatures ---
function Get-OpenBugSigs {
    $r = & $gh issue list --repo $Repo --label $label --state open --limit 100 --json number,title 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Verbose "gh issue list failed: $r"; return @{} }
    $set = @{}
    try {
        $arr = $r | ConvertFrom-Json
        foreach ($it in $arr) {
            if ($it.title -match '^\[dsh-bug\]\s+(.+)$') { $set[$Matches[1]] = $true }
        }
    } catch { }
    return $set
}

# --- ensure label exists ---
function Ensure-Label {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $gh label create --repo $Repo $label --description "DSH runtime bug auto-reported for Superagent fix" --color "d73a4a" 2>&1 | Out-Null
    # ignore "already exists" error (label present from a previous run)
    $ErrorActionPreference = $prev
}

# --- MAIN ---
Write-Host "=== DSH bug reporter | repo=$Repo | dryrun=$DryRun ==="
if (-not $DryRun) { Ensure-Label }
$openSigs = if ($DryRun) { @{} } else { Get-OpenBugSigs }

$newSigs = @{}   # sig -> {sev,title,first,last,count,stackExcerpt}
$genericNew = 0

foreach ($entry in @(@{path=$appLog; key="app"}, @{path=$backLog; key="backend"})) {
    $lastOff = if ($state["offsets"].ContainsKey($entry.key)) { [long]$state["offsets"][$entry.key] } else { 0 }
    $tail = Read-Tail $entry.path $lastOff
    $state["offsets"][$entry.key] = $tail.offset
    if ([string]::IsNullOrWhiteSpace($tail.text)) { continue }

    $lines = $tail.text -split "`n"
    $i = 0
    while ($i -lt $lines.Count) {
        $line = $lines[$i].TrimEnd("`r")
        if ([string]::IsNullOrWhiteSpace($line)) { $i++; continue }
        $sigInfo = Get-Signature $line
        if ($sigInfo) {
            $sig = $sigInfo.sig
            $isGeneric = $sig.StartsWith("generic:")
            # capture a small stack excerpt following the error line
            $excerpt = @($line)
            $j = $i + 1
            while ($j -lt $lines.Count -and $excerpt.Count -lt 12) {
                $l2 = $lines[$j].TrimEnd("`r")
                if ($l2 -match "^\s*at |^dsh-superagent|^Error|^    at ") { $excerpt += $l2; $j++ } else { break }
            }
            if ($state["seen"].ContainsKey($sig)) {
                $e = $state["seen"][$sig]
                $e["last"]  = (Get-Date).ToString("o")
                $e["count"] = [int]$e["count"] + 1
                # day-count tracking for persistent-recurrence detection
                $todayStr = (Get-Date).Date.ToString("yyyy-MM-dd")
                $lastDay = if ($e.ContainsKey("lastDay")) { [string]$e["lastDay"] } else { $null }
                if ($lastDay -eq $todayStr) {
                    $e["dayCount"] = [int]$e["dayCount"] + 1
                } else {
                    $e["lastDay"] = $todayStr
                    $e["dayCount"] = 1
                }
            } else {
                $state["seen"][$sig] = @{ first=(Get-Date).ToString("o"); last=(Get-Date).ToString("o"); count=1; lastDay=(Get-Date).ToString("yyyy-MM-dd"); dayCount=1 }
            }
            $newSigs[$sig] = @{ sev=$sigInfo.sev; title=$sigInfo.title; excerpt=($excerpt -join "`n"); count=[int]$state["seen"][$sig]["count"]; generic=$isGeneric }
        }
        $i++
    }
}

Write-Host "Detected signatures this run: $($newSigs.Count)"
foreach ($sig in $newSigs.Keys) {
    $info = $newSigs[$sig]
    $isOpen = $openSigs.ContainsKey($sig)
    Write-Host ("  [{0}] {1} (count={2}, open-issue={3}, generic={4})" -f $info.sev, $sig, $info.count, $isOpen, $info.generic)

    if ($DryRun) {
        Write-Host "    DRY-RUN: would create issue (skipped)"
        continue
    }
    if ($isOpen) {
        Write-Host "    SKIP: open issue already exists for $sig"
        continue
    }
    if ($info.generic -and $genericNew -ge $MaxNewGeneric) {
        Write-Host "    SKIP: generic-signature cap reached ($MaxNewGeneric per run)"
        continue
    }

    # --- re-arm / cooldown logic (replaces the old permanent suppression of #19/#20) ---
    # A signature that was reported before enters a cooldown; it stays silent until the
    # cooldown lapses (transient/one-off recurrence) OR it shows persistent recurrence
    # (>= $PersistentThreshold distinct local days), which re-arms it immediately.
    if ($state.ContainsKey("armed") -and $state["armed"].ContainsKey($sig)) {
        $armed = $state["armed"][$sig]
        $armedAt = $null
        if ($armed.PSObject.Properties['at']) { $armedAt = [datetime]$armed.at }
        $persistent = ($state["seen"].ContainsKey($sig) -and $state["seen"][$sig].ContainsKey("dayCount") -and [int]$state["seen"][$sig]["dayCount"] -ge $PersistentThreshold)
        if ($persistent) {
            Write-Host "    RE-ARM: $sig persistent recurrence ($($state['seen'][$sig]['dayCount']) distinct days >= $PersistentThreshold) -> reporting despite cooldown"
            $state["armed"].Remove($sig)
        } elseif ($armedAt) {
            $elapsed = ((Get-Date) - $armedAt).TotalHours
            if ($elapsed -lt $CooldownHours) {
                Write-Host "    COOLDOWN: $sig reported at $($armed.at), ${elapsed:n1}h ago < $CooldownHours h -> silent until $($armedAt.AddHours($CooldownHours).ToString('yyyy-MM-dd HH:mm'))"
                continue
            }
            Write-Host "    RE-ARM: $sig cooldown ($CooldownHours h) lapsed -> reporting again"
            $state["armed"].Remove($sig)
        }
    }

    $body = @(
        "**Auto-reported DSH runtime bug**"
        "`n**Signature:** ``$sig``"
        "`n**Severity:** $($info.sev)"
        "`n**First/Last seen / count:** $($state['seen'][$sig].first) / $($state['seen'][$sig].last) / $($info.count)"
        "`n**Log excerpt:**"
        "`n````"
        $info.excerpt
        "````"
        "`n---"
        "`n$AgentMention 请分析根因并提交修复 PR。修复后请更新本 issue 并说明部署步骤（本机安装的插件需更新后才会生效）。"
        "`n`n环境: Windows · DSH @deepseek-ai/dsh v0.2.2-rc.1 · Superagent 0.1.0"
    ) -join "`n"

    $bodyFile = Join-Path $env:TEMP ("dsh-bug-body-" + [guid]::NewGuid().ToString("N") + ".md")
    [System.IO.File]::WriteAllText($bodyFile, $body, (New-Object System.Text.UTF8Encoding($false)))

    $title = "[dsh-bug] $sig"
    # PS 5.1 mangles native args containing double quotes or apostrophes when the value
    # also has spaces; a broken arg aborts the whole run and later signatures are lost.
    # The body keeps the readable (single-quoted) signature; the TITLE is stripped of both
    # quote chars and collapsed so gh.exe always receives one clean argument.
    $title = $title -replace '["'']', ''
    $title = $title -replace '\s+', ' '
    if ($title.Length -gt 120) { $title = $title.Substring(0, 120) }
    $title = $title.Trim()

    $bodyFile = Join-Path $env:TEMP ("dsh-bug-body-" + [guid]::NewGuid().ToString("N") + ".md")
    [System.IO.File]::WriteAllText($bodyFile, $body, (New-Object System.Text.UTF8Encoding($false)))

    # Run gh in a Continue-error island: a gh stderr line must not abort the loop and
    # starve the remaining signatures in this run.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $r = & $gh issue create --repo $Repo --title $title --body-file $bodyFile --label $label 2>&1 | Out-String
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
    if ($rc -eq 0) {
        Write-Host "    CREATED: $r"
        if ($info.generic) { $genericNew++ }
        # record the arm timestamp so this signature enters cooldown (suppressed until lapsed/persistent)
        if (-not $state.ContainsKey("armed")) { $state["armed"] = @{} }
        $state["armed"][$sig] = @{ at = (Get-Date).ToString("o"); issue = ($r -replace '\s+', '').Trim() }
    } else {
        Write-Host "    CREATE FAILED: $r"
    }
}

# persist state (dry-run is side-effect-free: it must NOT advance offsets or mark signatures seen)
$stateDirFull = $stateDir
if (-not (Test-Path $stateDirFull)) { New-Item -ItemType Directory -Path $stateDirFull -Force | Out-Null }
if (-not $DryRun) {
    [System.IO.File]::WriteAllText($stateFile, ($state | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "=== state saved: $stateFile (offsets updated) ==="
} else {
    Write-Host "=== DRY-RUN: state NOT saved (no side effects) ==="
}
