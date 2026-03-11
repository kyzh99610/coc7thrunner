[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Python312Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$pyvenvConfig = Join-Path $venvRoot "pyvenv.cfg"
$tempRoot = Join-Path $projectRoot ".tmp"

function Assert-PathExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    if (-not (Test-Path -LiteralPath $PathValue -PathType Leaf)) {
        throw "Python312Path does not exist: $PathValue"
    }
}

function Get-ResolvedPathString {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    return (Resolve-Path -LiteralPath $PathValue).Path
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $joinedArguments = $Arguments -join " "
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $joinedArguments"
    }
}

Assert-PathExists -PathValue $Python312Path
$resolvedPython312 = Get-ResolvedPathString -PathValue $Python312Path

Write-Host "Using explicit Python candidate: $resolvedPython312"

$versionCheck = & $resolvedPython312 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}'); print(sys.executable)"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to execute Python interpreter: $resolvedPython312"
}

if ($versionCheck.Count -lt 2) {
    throw "Could not read version information from: $resolvedPython312"
}

$pythonVersion = $versionCheck[0].Trim()
$pythonExecutable = $versionCheck[1].Trim()

if (-not $pythonVersion.StartsWith("3.12.")) {
    throw "Python312Path must point to Python 3.12.x. Detected: $pythonVersion at $pythonExecutable"
}

if ($pythonExecutable -match "WindowsApps") {
    throw "Python312Path points to a WindowsApps interpreter. Use a real Python 3.12 installation path instead: $pythonExecutable"
}

New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$env:TMP = $tempRoot
$env:TEMP = $tempRoot

if ($env:VIRTUAL_ENV) {
    $activeVenv = [System.IO.Path]::GetFullPath($env:VIRTUAL_ENV)
    $targetVenv = [System.IO.Path]::GetFullPath($venvRoot)
    if ($activeVenv -eq $targetVenv) {
        throw "The target .venv is currently active. Deactivate it and rerun the repair script."
    }
}

Push-Location $projectRoot
try {
    if (Test-Path -LiteralPath $venvRoot) {
        Write-Host "Removing existing virtual environment at $venvRoot"
        Remove-Item -LiteralPath $venvRoot -Recurse -Force
    }

    Write-Host "Creating new virtual environment from $resolvedPython312"
    Invoke-Checked -FilePath $resolvedPython312 -Arguments @("-m", "venv", $venvRoot)

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Virtual environment creation completed, but missing interpreter: $venvPython"
    }

    Write-Host "Upgrading pip inside .venv"
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")

    Write-Host "Installing project and dev dependencies"
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]")

    Write-Host ""
    Write-Host "Repaired environment summary"
    Write-Host "python --version:"
    Invoke-Checked -FilePath $venvPython -Arguments @("--version")

    Write-Host ""
    Write-Host "sys.executable:"
    Invoke-Checked -FilePath $venvPython -Arguments @("-c", "import sys; print(sys.executable)")

    Write-Host ""
    Write-Host ".venv\\pyvenv.cfg:"
    Get-Content -LiteralPath $pyvenvConfig

    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "1. .\\.venv\\Scripts\\Activate.ps1"
    Write-Host "2. python -m pytest -q"
    Write-Host "3. python -m uvicorn coc_runner.main:create_app --factory --reload"
}
finally {
    Pop-Location
}
