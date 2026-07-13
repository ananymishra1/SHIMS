#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install Ollama and pull best models for AMD Ryzen AI MAX+ 395 + 128GB RAM
.DESCRIPTION
    One-click installer for Ollama + recommended LLM models for SHIMS.
    Run in PowerShell (Admin):  powershell -ExecutionPolicy Bypass -File .\scripts\install_ollama.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SHIMS Ollama Beast-Mode Installer" -ForegroundColor Cyan
Write-Host "  Target: 128GB AMD Ryzen AI MAX+ 395" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# --- Check if Ollama is already installed ---
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    Write-Host "[OK] Ollama found: $($ollama.Source)" -ForegroundColor Green
} else {
    Write-Host "[INFO] Ollama not found. Installing via winget..." -ForegroundColor Yellow
    try {
        # Use explicit winget source to avoid msstore certificate issues
        winget install --id Ollama.Ollama --source winget --accept-source-agreements --accept-package-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollama) {
            # Fallback paths
            $possiblePaths = @(
                "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
                "$env:LOCALAPPDATA\Ollama\ollama.exe",
                "C:\Program Files\Ollama\ollama.exe"
            )
            foreach ($p in $possiblePaths) {
                if (Test-Path $p) { $ollama = $p; break }
            }
        }
    } catch {
        Write-Host "[ERROR] winget install failed." -ForegroundColor Red
    }
}

if (-not $ollama) {
    Write-Host "[INFO] Falling back to direct download from ollama.com..." -ForegroundColor Yellow
    $installerUrl = "https://ollama.com/download/OllamaSetup.exe"
    $installerPath = "$env:TEMP\OllamaSetup.exe"
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-Host "[INFO] Running Ollama installer..." -ForegroundColor Yellow
        Start-Process -FilePath $installerPath -ArgumentList "/S" -Wait
        Remove-Item $installerPath -ErrorAction SilentlyContinue
        # Check again
        $possiblePaths = @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:LOCALAPPDATA\Ollama\ollama.exe",
            "C:\Program Files\Ollama\ollama.exe"
        )
        foreach ($p in $possiblePaths) {
            if (Test-Path $p) { $ollama = $p; break }
        }
    } catch {
        Write-Host "[ERROR] Direct download failed. Please install manually from https://ollama.com/download" -ForegroundColor Red
        exit 1
    }
}

if (-not $ollama) {
    Write-Host "[ERROR] Ollama could not be found after install." -ForegroundColor Red
    exit 1
}

$ollamaExe = if ($ollama.Source) { $ollama.Source } else { $ollama }
Write-Host "[OK] Using Ollama: $ollamaExe" -ForegroundColor Green

# --- Start Ollama service if not running ---
$svc = Get-Process ollama -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "[INFO] Starting Ollama service..." -ForegroundColor Yellow
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

# --- Pull models ---
$models = @(
    @{ Name = "llama3.3:70b";     Desc = "Primary - best quality (~42GB Q4)" },
    @{ Name = "qwen3:32b";       Desc = "Fast fallback - excellent reasoning (~20GB Q4)" },
    @{ Name = "deepseek-coder-v2:16b"; Desc = "Coding specialist (~12GB Q4)" },
    @{ Name = "gemma3:27b";      Desc = "Vision + multimodal (~18GB Q4)" },
    @{ Name = "command-r-plus:104b"; Desc = "Instruction monster (~60GB Q4)" }
)

Write-Host "`n[SHIMS] Pulling models for your 128GB beast..." -ForegroundColor Cyan
foreach ($m in $models) {
    Write-Host "`n[SHIMS] Pulling $($m.Name) - $($m.Desc)" -ForegroundColor Yellow
    & $ollamaExe pull $m.Name
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] Failed to pull $($m.Name), skipping..." -ForegroundColor Red
    } else {
        Write-Host "[OK] $($m.Name) ready!" -ForegroundColor Green
    }
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Ollama + Models Ready!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "`nTo start SHIMS, run in Git Bash or CMD:" -ForegroundColor White
Write-Host "  cd C:\Users\direc\Documents\kimi\workspace\SHIMS" -ForegroundColor Gray
Write-Host "  .venv\Scripts\python scripts\start_shims.py" -ForegroundColor Gray
Write-Host "`nThen open: http://127.0.0.1:8010" -ForegroundColor Gray
