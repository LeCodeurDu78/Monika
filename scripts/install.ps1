# Installe Monika dans le dossier du projet : venv, dépendances, .env et wizard.
#   .\scripts\install.ps1
#   .\scripts\install.ps1 -NoSetup
#   .\scripts\install.ps1 -Voice
param([switch]$NoSetup, [switch]$Voice)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.12+ est requis." }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "creation de .venv"
  python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\pip.exe" install -r requirements.txt

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host ".env cree depuis .env.example"
}
New-Item -ItemType Directory -Force -Path "config" | Out-Null
if (-not (Test-Path "config\settings.json")) {
  Copy-Item "config\settings.example.json" "config\settings.json"
}
if ($Voice) { & ".\scripts\install_gptsovits.ps1" }
if (-not $NoSetup) { & ".venv\Scripts\python.exe" -m monika --setup }
Write-Host "Installation terminee."
