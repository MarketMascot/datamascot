<#
.SYNOPSIS
    Read secrets from .env and push them to GitHub Actions secrets via `gh`.

.DESCRIPTION
    Parses the local .env file, extracts the keys listed in $SecretNames,
    and uploads each one to the configured GitHub repo using `gh secret set`.

    Values are piped to gh via stdin so they never appear in:
      - PowerShell command-line history
      - Process listings (Get-Process / ps)
      - Console transcripts

.PARAMETER EnvFile
    Path to the .env file. Default: d:\dastock\.env

.PARAMETER Repo
    GitHub repo in OWNER/NAME format. Default: MarketMascot/datamascot

.PARAMETER DryRun
    If set, print what would be uploaded but don't call `gh`.

.EXAMPLE
    .\scripts\push_secrets.ps1
    .\scripts\push_secrets.ps1 -Repo MarketMascot/datamascot_prod
    .\scripts\push_secrets.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$Repo = "MarketMascot/datamascot",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Whitelist — only these keys get uploaded, even if .env contains more.
# Edit this list if you add new secrets to the workflows.
$SecretNames = @(
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DHAN_CLIENT_ID",
    "DHAN_ACCESS_TOKEN"
)

# ─── Sanity checks ───────────────────────────────────────────────────────────

if (-not (Test-Path $EnvFile)) {
    Write-Error ".env file not found at: $EnvFile"
    exit 1
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Error "gh CLI not found. Install from https://cli.github.com/"
    exit 1
}

# Confirm gh is authenticated
$authStatus = & gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "gh is not authenticated. Run: gh auth login"
    exit 1
}

Write-Host "Repo:       $Repo" -ForegroundColor Cyan
Write-Host "Env file:   $EnvFile" -ForegroundColor Cyan
Write-Host "Dry run:    $DryRun" -ForegroundColor Cyan
Write-Host ""

# ─── Parse .env ──────────────────────────────────────────────────────────────

$envMap = @{}
$lineNum = 0
foreach ($line in Get-Content $EnvFile) {
    $lineNum++
    $trimmed = $line.Trim()

    # Skip blank lines and comments
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }

    # KEY=value (value can be quoted; strip optional surrounding quotes)
    if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $key = $matches[1]
        $val = $matches[2]

        # Strip optional surrounding single or double quotes
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or
            ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }

        $envMap[$key] = $val
    }
}

# ─── Push each whitelisted secret ────────────────────────────────────────────

$pushed = 0
$skipped = 0
$failed = 0

foreach ($name in $SecretNames) {
    if (-not $envMap.ContainsKey($name)) {
        Write-Host "  SKIP  $name (not in .env)" -ForegroundColor Yellow
        $skipped++
        continue
    }

    $value = $envMap[$name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host "  SKIP  $name (empty value)" -ForegroundColor Yellow
        $skipped++
        continue
    }

    # Print masked preview: first 4 + last 4 chars
    $preview = if ($value.Length -le 10) {
        "***"
    } else {
        "$($value.Substring(0,4))...$($value.Substring($value.Length-4,4)) ($($value.Length) chars)"
    }

    if ($DryRun) {
        Write-Host "  WOULD $name = $preview" -ForegroundColor Magenta
        continue
    }

    # Pipe value to gh via stdin so it never appears on the command line.
    # `gh secret set NAME --repo X --body -` reads body from stdin.
    try {
        $value | & gh secret set $name --repo $Repo --body - 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  OK    $name = $preview" -ForegroundColor Green
            $pushed++
        } else {
            Write-Host "  FAIL  $name (gh exit $LASTEXITCODE)" -ForegroundColor Red
            $failed++
        }
    } catch {
        Write-Host "  FAIL  $name : $_" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
Write-Host "Summary: $pushed pushed, $skipped skipped, $failed failed" -ForegroundColor Cyan

if ($failed -gt 0) { exit 1 }
