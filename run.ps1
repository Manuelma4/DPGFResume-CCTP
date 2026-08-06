param(
    [int]$Port = 8070,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendIndex = Join-Path $projectRoot "frontend\dist\index.html"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Environnement Python absent. Exécutez d'abord .\setup.ps1"
}
if (-not (Test-Path -LiteralPath $frontendIndex)) {
    throw "Frontend absent. Exécutez d'abord .\setup.ps1"
}

if ($Restart) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
}

Set-Location $projectRoot
Write-Host "DPGF Résumé CCTP : http://127.0.0.1:$Port" -ForegroundColor Green
& $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port $Port

