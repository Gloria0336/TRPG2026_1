param(
    [string]$Token = $env:CLOUDFLARED_TOKEN
)

$ErrorActionPreference = "Stop"

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "cloudflared was not found in PATH." -ForegroundColor Yellow
    Write-Host "Install it first, then rerun this script:"
    Write-Host "  winget install --id Cloudflare.cloudflared"
    exit 1
}

if (-not $Token) {
    Write-Host "Cloudflare connector token was not provided." -ForegroundColor Yellow
    Write-Host "Run with:"
    Write-Host "  .\scripts\start_cloudflare_connector.ps1 -Token '<token-from-cloudflare>'"
    Write-Host ""
    Write-Host "Or set it for this PowerShell session first:"
    Write-Host "  `$env:CLOUDFLARED_TOKEN='<token-from-cloudflare>'"
    Write-Host "  .\scripts\start_cloudflare_connector.ps1"
    exit 1
}

Write-Host "Starting Cloudflare dashboard-managed connector ..."
& $cloudflared.Source tunnel --no-autoupdate run --token $Token
exit $LASTEXITCODE
