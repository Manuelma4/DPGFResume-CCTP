$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Environnement Python absent. Exécutez .\setup.ps1"
}

Set-Location $projectRoot
& $pythonPath -m py_compile app\config.py app\extractors.py app\parser.py app\llm.py app\store.py app\auth.py app\excel_export.py app\main.py
& $pythonPath -m unittest discover -s tests -v

Push-Location (Join-Path $projectRoot "frontend")
try {
    npm.cmd run build
}
finally {
    Pop-Location
}

Write-Host "Validation terminée avec succès." -ForegroundColor Green

