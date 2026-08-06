param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Host "DPGF Résumé CCTP - installation" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $pythonPath)) {
    $venvTarget = Join-Path $projectRoot ".venv"
    try {
        py -3.11 -m venv $venvTarget
    }
    catch {
        Write-Host "Le lanceur py est indisponible, recherche d'un interpréteur Python..." -ForegroundColor Yellow
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        $workspacePython = Join-Path $projectRoot "..\FTMgen\.venv\Scripts\python.exe"
        if ($pythonCommand) {
            & $pythonCommand.Source -m venv $venvTarget
        }
        elseif (Test-Path -LiteralPath $workspacePython) {
            & $workspacePython -m venv $venvTarget
        }
        else {
            throw "Python 3.11+ est introuvable. Installez Python puis relancez setup.ps1."
        }
    }
}

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -r (Join-Path $projectRoot "requirements.txt")

if (-not $SkipFrontend) {
    Push-Location (Join-Path $projectRoot "frontend")
    try {
        npm.cmd install
        npm.cmd run build
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "output") | Out-Null
Write-Host "Installation terminée." -ForegroundColor Green
Write-Host "Lancez ensuite .\run.ps1"
