# SHIMS Clean Start — Launch both Omni and Enterprise servers in separate windows
# Usage:
#   .\scripts\start_both.ps1              # Local only (127.0.0.1) — default
#   .\scripts\start_both.ps1 -Network     # LAN accessible (0.0.0.0) — other devices on WiFi can connect
#   .\scripts\start_both.ps1 -Port 8022   # Custom Enterprise port
param(
    [switch]$Network,
    [int]$EnterprisePort = 8021,
    [int]$OmniPort = 8010
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

if (!(Test-Path .env)) { Copy-Item .env.example .env }

# Pick host binding
$HostBind = if ($Network) { '0.0.0.0' } else { '127.0.0.1' }

# Kill any existing uvicorn processes to free ports
try { Get-Process python | Where-Object { $_.CommandLine -like "*uvicorn*" } | Stop-Process -Force } catch {}
Start-Sleep -Seconds 2

# Start Enterprise in a new window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root'; . '.\.venv\Scripts\Activate.ps1'; python -m uvicorn shims_enterprise.app:app --host $HostBind --port $EnterprisePort"
) -WindowStyle Normal

# Start Omni in a new window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root'; . '.\.venv\Scripts\Activate.ps1'; python -m uvicorn backend.app.main:app --host $HostBind --port $OmniPort"
) -WindowStyle Normal

Write-Host "SHIMS servers starting..." -ForegroundColor Green
if ($Network) {
    $localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
    Write-Host "Enterprise (this PC):  http://127.0.0.1:$EnterprisePort/login" -ForegroundColor Cyan
    Write-Host "Enterprise (network):  http://${localIP}:$EnterprisePort/login" -ForegroundColor Cyan
    Write-Host "Omni (this PC):        http://127.0.0.1:$OmniPort/app" -ForegroundColor Cyan
    Write-Host "Omni (network):        http://${localIP}:$OmniPort/app" -ForegroundColor Cyan
} else {
    Write-Host "Enterprise: http://127.0.0.1:$EnterprisePort/login" -ForegroundColor Cyan
    Write-Host "Omni:       http://127.0.0.1:$OmniPort/app" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "Demo logins (all passwords: admin123)" -ForegroundColor Yellow
Write-Host "  admin / rd / qc / warehouse / production / procurement / qa" -ForegroundColor Yellow
