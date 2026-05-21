Param()

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:PYTHON) {
    $BootstrapPython = $env:PYTHON
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $BootstrapPython = "python"
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $BootstrapPython = "py"
}
else {
    throw "python not found on PATH"
}
$VenvDir = if ($env:CHINALAW_VENV) { $env:CHINALAW_VENV } else { Join-Path $RepoRoot ".venv" }
$BinDir = if ($env:CHINALAW_BIN_DIR) { $env:CHINALAW_BIN_DIR } else { Join-Path $HOME ".local\bin" }
$PythonBin = Join-Path $VenvDir "Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

if (-not (Test-Path $PythonBin)) {
    & $BootstrapPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "failed to create virtualenv with $BootstrapPython"
    }
}

& $PythonBin -m pip install -e $RepoRoot
if ($LASTEXITCODE -eq 0) {
    Write-Host "editable install ok: $RepoRoot ($PythonBin)"
}
else {
    Write-Warning "editable install failed; wrapper will fall back to PYTHONPATH mode"
}

function Write-ChinalawShim {
    Param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module
    )

    $cmdPath = Join-Path $BinDir "$Name.cmd"
    $ps1Path = Join-Path $BinDir "$Name.ps1"
    $srcPath = Join-Path $RepoRoot "src"

    $cmdContent = @"
@echo off
setlocal
set "PYTHON=$PythonBin"
set "PYTHONPATH=$srcPath;%PYTHONPATH%"
"%PYTHON%" -m $Module %*
exit /b %ERRORLEVEL%
"@
    Set-Content -Path $cmdPath -Value $cmdContent -Encoding ASCII

    $escapedPython = $PythonBin.Replace("'", "''")
    $escapedSrc = $srcPath.Replace("'", "''")
    $ps1Content = @"
`$Python = '$escapedPython'
`$env:PYTHONPATH = '$escapedSrc' + [IO.Path]::PathSeparator + `$env:PYTHONPATH
& `$Python -m $Module @args
exit `$LASTEXITCODE
"@
    Set-Content -Path $ps1Path -Value $ps1Content -Encoding UTF8

    Write-Host "installed wrapper: $cmdPath"
    Write-Host "installed wrapper: $ps1Path"
}

Write-ChinalawShim -Name "chinalaw" -Module "chinalaw"
Write-ChinalawShim -Name "chinalaw-mcp" -Module "chinalaw.mcp"

$pathParts = ($env:PATH -split [IO.Path]::PathSeparator) | Where-Object { $_ }
$binOnPath = $pathParts | Where-Object {
    try { (Resolve-Path $_ -ErrorAction Stop).Path -eq (Resolve-Path $BinDir).Path } catch { $false }
}
if (-not $binOnPath) {
    Write-Warning "chinalaw shims are not on PATH. Add this user PATH entry and restart the terminal:"
    Write-Host "  [Environment]::SetEnvironmentVariable('Path', '$BinDir;' + [Environment]::GetEnvironmentVariable('Path', 'User'), 'User')"
}

& (Join-Path $BinDir "chinalaw.cmd") --version
if ($LASTEXITCODE -ne 0) {
    throw "installed chinalaw wrapper failed"
}
