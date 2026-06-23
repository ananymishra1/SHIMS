$cwd = Split-Path -Parent $MyInvocation.MyCommand.Path
$cwd = Resolve-Path (Join-Path $cwd '..')
$exe = Join-Path $cwd '.venv\Scripts\python.exe'
$out = Join-Path $cwd 'logs\omni_server.log'
$err = Join-Path $cwd 'logs\omni_server.err'
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$proc = Start-Process -FilePath $exe -ArgumentList '-m','uvicorn','backend.app.main:app','--host','0.0.0.0','--port','8010' -WorkingDirectory $cwd -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
while (!$proc.HasExited) {
    Start-Sleep -Seconds 30
    Write-Host "omni-keepalive $(Get-Date -Format 'HH:mm:ss')"
}
Write-Host "Omni process exited with code $($proc.ExitCode)"
