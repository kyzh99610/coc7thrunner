param(
    [switch]$Windowed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$entryScript = Join-Path $projectRoot "src\coc_runner\internal_local_launcher.py"
$variantName = if ($Windowed) { "CoCRunnerInternalLauncherWindowed" } else { "CoCRunnerInternalLauncher" }
$artifactRoot = if ($Windowed) {
    Join-Path $projectRoot "build-artifacts\internal-launcher-exe-windowed"
} else {
    Join-Path $projectRoot "build-artifacts\internal-launcher-exe"
}
$distRoot = Join-Path $artifactRoot "dist"
$workRoot = Join-Path $artifactRoot "build"
$specRoot = Join-Path $artifactRoot "spec"
$srcPath = Join-Path $projectRoot "src"
$tmpRoot = Join-Path $projectRoot ".tmp"
$consoleFlag = if ($Windowed) { "--windowed" } else { "--console" }
$pythonCandidates = @(
    (Join-Path $projectRoot ".tools\python312\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
)
$pythonExe = $null

foreach ($candidate in $pythonCandidates) {
    if (-not (Test-Path $candidate)) {
        continue
    }
    & $candidate -c "import tkinter as tk; root = tk.Tk(); root.destroy()" *> $null
    if ($LASTEXITCODE -eq 0) {
        $pythonExe = $candidate
        break
    }
}

if (-not $pythonExe) {
    throw "Could not find a usable Python runtime with working Tk support for the internal launcher build."
}

if (-not (Test-Path $entryScript)) {
    throw "Missing launcher entry script: $entryScript"
}

$null = New-Item -ItemType Directory -Force -Path $artifactRoot, $distRoot, $workRoot, $specRoot, $tmpRoot

$probe = & $pythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in the selected build runtime: $pythonExe"
}

$env:PYTHONPATH = $srcPath
$env:TMP = $tmpRoot
$env:TEMP = $tmpRoot

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    $consoleFlag,
    "--name", $variantName,
    "--distpath", $distRoot,
    "--workpath", $workRoot,
    "--specpath", $specRoot,
    "--paths", $srcPath,
    "--hidden-import", "tkinter",
    "--hidden-import", "tkinter.ttk",
    "--hidden-import", "tkinter.scrolledtext",
    $entryScript
)

Push-Location $projectRoot
try {
    & $pythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$exePath = Join-Path $distRoot "$variantName\$variantName.exe"
if (-not (Test-Path $exePath)) {
    throw "Expected launcher exe was not produced: $exePath"
}

Write-Host "Built internal launcher exe:"
Write-Host $exePath
