param(
    [switch]$Quick,
    [string]$Config = ".cloudflare\trpg2026-tunnel.yml"
)

$ErrorActionPreference = "Stop"

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "cloudflared was not found in PATH." -ForegroundColor Yellow
    Write-Host "Install it first, then rerun this script:"
    Write-Host "  winget install --id Cloudflare.cloudflared"
    exit 1
}

if ($Quick) {
    Write-Host "Starting a temporary Cloudflare quick tunnel to http://127.0.0.1:8001 ..."
    Write-Host "This is good for public reachability tests, but not stable enough for Discord OAuth."
    & $cloudflared.Source tunnel --url http://127.0.0.1:8001
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $Config)) {
    Write-Host "Tunnel config not found: $Config" -ForegroundColor Yellow
    Write-Host "Copy ops\cloudflare\tunnel.example.yml to $Config and fill in your tunnel + hostname."
    exit 1
}

Write-Host "Starting named Cloudflare tunnel using $Config ..."
& $cloudflared.Source tunnel --config $Config run
exit $LASTEXITCODE
