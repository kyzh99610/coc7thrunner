param()

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonRoot = Join-Path $projectRoot ".tools\python312"
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvScripts = Join-Path $venvRoot "Scripts"
$tempRoot = Join-Path $projectRoot ".tmp"

if (-not (Test-Path (Join-Path $pythonRoot "python.exe"))) {
    throw "Missing project Python runtime at $pythonRoot"
}

if (-not (Test-Path $venvPython)) {
    throw "Missing project virtual environment at $venvPython"
}

New-Item -ItemType Directory -Force $tempRoot | Out-Null

$env:TMP = $tempRoot
$env:TEMP = $tempRoot

$pathEntries = @($venvScripts, $pythonRoot)
$existingPath = $env:PATH -split ';' | Where-Object { $_ }
$env:PATH = (($pathEntries + $existingPath) | Select-Object -Unique) -join ';'

Write-Output "Project Python active: $venvPython"
Write-Output "Temp directory: $tempRoot"
