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
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function Test-PipAvailable {
    Param([Parameter(Mandatory = $true)][string]$Python)
    & $Python -m pip --version *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path $PythonBin)) {
    & $BootstrapPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "failed to create virtualenv with $BootstrapPython; wrappers will use PYTHONPATH fallback mode"
    }
}

if ((Test-Path $PythonBin) -and -not (Test-PipAvailable -Python $PythonBin)) {
    & $PythonBin -m ensurepip --upgrade *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "bootstrapped pip inside $VenvDir"
    }
    else {
        Write-Warning "virtualenv exists but pip is unavailable; wrappers will use PYTHONPATH fallback mode"
    }
}

if (Test-Path $PythonBin) {
    & $PythonBin -m pip install -e $RepoRoot
    if ($LASTEXITCODE -eq 0) {
        Write-Host "editable install ok: $RepoRoot ($PythonBin)"
    }
    else {
        Write-Warning "editable install failed; wrapper will fall back to PYTHONPATH mode"
    }
}
else {
    Write-Warning "editable install skipped; wrapper will fall back to PYTHONPATH mode"
    $PythonBin = if ($env:PYTHON) { $env:PYTHON } else { $BootstrapPython }
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
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
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
`$env:PYTHONUTF8 = '1'
`$env:PYTHONIOENCODING = 'utf-8'
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
