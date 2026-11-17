# Regression tests for scripts/dsh-bug-report.ps1.
#
# The reporter is a PowerShell 5.1 script with a UTF-8 BOM (it contains Chinese
# text in its comments), so these tests use the same interpreter the scheduled
# task uses: `powershell.exe`. Each test extracts the real Get-Signature
# function from the script file and calls it, so the assertions track the
# shipped implementation rather than a copy of it.
#
# Run with: powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/bug-reporter.spec.ps1

param(
    [string]$Reporter
)

$ErrorActionPreference = 'Stop'

if (-not $Reporter) {
    $Reporter = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot (Join-Path '..' (Join-Path 'scripts' 'dsh-bug-report.ps1'))))
}

if (-not (Test-Path $Reporter)) { throw "Reporter script not found: $Reporter" }

# Parse the reporter and pull the Get-Signature function definition out of its AST.
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Reporter, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "Reporter did not parse: $($errors | ForEach-Object Message | Out-String)" }
$fn = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-Signature'
}, $true) | Select-Object -First 1
if (-not $fn) { throw 'Reporter has no Get-Signature function' }

# The function closes over $dshHome, the Harness home it resolves profile
# manifests from. Bind it in this scope before defining the function.
$dshHome = $null
. ([scriptblock]::Create($fn.Extent.Text))

$passed = 0
$failed = 0
function Assert-Equal($name, $expected, $actual) {
    if ($expected -eq $actual) {
        Write-Host "  PASS  $name"
        $script:passed++
    } else {
        Write-Host "  FAIL  $name (expected '$expected', got '$actual')"
        $script:failed++
    }
}

function New-FakeHome {
    return (Join-Path ([System.IO.Path]::GetTempPath()) ("bug reporter test " + [guid]::NewGuid().ToString('N')))
}

function Add-ProfileManifest($homePath, $profile) {
    $dir = Join-Path $homePath (Join-Path 'profiles' $profile)
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    Set-Content -Path (Join-Path $dir 'package.json') -Value '{}' -Encoding UTF8
}

$missing = 'Error: dsh: profile "superagent" does not exist; create it with ''dsh plugin --profile superagent add <package>'''
$ghost = 'Error: dsh: profile "ghost" does not exist; create it with ''dsh plugin --profile ghost add <package>'''

Write-Host 'Get-Signature: a stale missing-profile line is ignored when the profile now exists'
$root = New-FakeHome
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    Add-ProfileManifest $root 'superagent'
    $dshHome = $root
    $stale = Get-Signature $missing
    Assert-Equal 'existing superagent profile yields no signature' $null $stale
    $other = Get-Signature $ghost
    Assert-Equal 'missing ghost profile still reported' 'missing-profile:ghost' $other.sig
} finally {
    Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'Get-Signature: a missing profile that is still absent is reported'
$root = New-FakeHome
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $dshHome = $root
    $live = Get-Signature $missing
    Assert-Equal 'absent superagent profile reported' 'missing-profile:superagent' $live.sig
} finally {
    Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "$passed passed, $failed failed"
if ($failed -gt 0) { exit 1 }
