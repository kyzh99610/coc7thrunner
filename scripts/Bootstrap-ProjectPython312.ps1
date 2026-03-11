param()

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonRoot = Join-Path $projectRoot ".tools\python312"
$pythonExe = Join-Path $pythonRoot "python.exe"
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$sitePackages = Join-Path $venvRoot "Lib\site-packages"
$pipWheel = Join-Path $pythonRoot "Lib\ensurepip\_bundled\pip-25.0.1-py3-none-any.whl"
$tempRoot = Join-Path $projectRoot ".tmp"

if (-not (Test-Path $pythonExe)) {
    throw "Missing project Python runtime at $pythonExe"
}

if (-not (Test-Path $pipWheel)) {
    throw "Missing bundled pip wheel at $pipWheel"
}

New-Item -ItemType Directory -Force $tempRoot | Out-Null
$env:TMP = $tempRoot
$env:TEMP = $tempRoot

if (Test-Path $venvRoot) {
    Remove-Item -Recurse -Force $venvRoot
}

& $pythonExe -m venv $venvRoot --without-pip

if (Test-Path (Join-Path $sitePackages "pip")) {
    Remove-Item -Recurse -Force (Join-Path $sitePackages "pip")
}

Get-ChildItem $sitePackages -Filter "pip-*.dist-info" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Expand-Archive -LiteralPath $pipWheel -DestinationPath $sitePackages -Force

& $venvPython -m pip install --upgrade pip setuptools wheel
& $venvPython -m pip install -e ".[dev]"
