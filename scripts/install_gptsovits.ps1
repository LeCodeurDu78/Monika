# Installe GPT-SoVITS dans vendor\GPT-SoVITS (commit épinglé), avec son propre venv.
#   .\scripts\install_gptsovits.ps1
#   .\scripts\install_gptsovits.ps1 -Models
param([switch]$Models)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dir = if ($env:GPT_SOVITS_DIR) { $env:GPT_SOVITS_DIR } else { Join-Path $Root "vendor\GPT-SoVITS" }
$Commit = "b4570b876f5c"

if (-not (Test-Path (Join-Path $Dir ".git"))) {
  Write-Host "clone de GPT-SoVITS dans $Dir"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dir) | Out-Null
  git clone https://github.com/RVC-Boss/GPT-SoVITS $Dir
}
Write-Host "commit $Commit"
git -C $Dir fetch --quiet origin
git -C $Dir checkout --quiet $Commit

$Py = Join-Path $Dir ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Host "environnement Python de GPT-SoVITS"
  python -m venv (Join-Path $Dir ".venv")
}
& $Py -m pip install --upgrade pip
& (Join-Path $Dir ".venv\Scripts\pip.exe") install -r (Join-Path $Dir "requirements.txt")
if ($Models) {
  & (Join-Path $Dir ".venv\Scripts\pip.exe") install --upgrade huggingface_hub
  & (Join-Path $Dir ".venv\Scripts\huggingface-cli.exe") download lj1995/GPT-SoVITS --local-dir (Join-Path $Dir "pretrained_models")
}
Write-Host ""
Write-Host "GPT-SoVITS est prêt dans $Dir"
Write-Host "  Demarrage : cd $Dir; .venv\Scripts\python.exe api_v2.py -a 127.0.0.1 -p 9880"
Write-Host "  Avec Monika : lancer-monika.sh (ou la commande equivalente Windows)"
