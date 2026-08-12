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

# pyproject.toml 声明 requires-python >= 3.10；低于此版本时 venv 里的 pip 往往过旧、
# 不支持 PEP 660 editable install，报错还会伪装成「setup.py not found」这类误导信息。
# 因此在创建 venv 前显式校验解释器版本（与 scripts/install-local 一致，见 issue #4）。
$MinPyMajor = 3
$MinPyMinor = 10

function Invoke-Python {
    Param(
        [Parameter(Mandatory = $true)][string[]]$Python,
        [Parameter(Mandatory = $true)][string[]]$PyArgs
    )
    $rest = @($Python | Select-Object -Skip 1)
    & $Python[0] @rest @PyArgs
}

function Get-PythonVersionParts {
    # 返回 @(major, minor)；解释器不可用/异常时返回 $null。
    # 让 Python 始终退出 0（只打印版本），版本比较放到 PowerShell 侧，避免
    # PS 7.4+ 下原生命令非零退出（$PSNativeCommandUseErrorActionPreference）被
    # 当成终止错误抛出，从而无法用 $LASTEXITCODE 判定。
    Param([Parameter(Mandatory = $true)][string[]]$Python)
    $out = Invoke-Python -Python $Python -PyArgs @('-c', 'import sys; print("%d %d" % sys.version_info[:2])') 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
        $parts = ("$out").Trim() -split '\s+'
        if ($parts.Count -ge 2) { return @([int]$parts[0], [int]$parts[1]) }
    }
    return $null
}

function Test-PythonVersion {
    Param([Parameter(Mandatory = $true)][string[]]$Python)
    $parts = Get-PythonVersionParts -Python $Python
    if ($null -eq $parts) { return $false }
    if ($parts[0] -gt $MinPyMajor) { return $true }
    return ($parts[0] -eq $MinPyMajor -and $parts[1] -ge $MinPyMinor)
}

function Get-PythonVersion {
    Param([Parameter(Mandatory = $true)][string[]]$Python)
    $out = Invoke-Python -Python $Python -PyArgs @('-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])') 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return ("$out").Trim() }
    return "unknown"
}

# 确保用于创建 venv 的解释器满足 requires-python；仅在需要新建 venv 时调用。
function Resolve-BootstrapPython {
    Param([Parameter(Mandatory = $true)][string]$Current)
    if (Test-PythonVersion -Python @($Current)) { return $Current }
    $found = Get-PythonVersion -Python @($Current)
    $guidance = "Install Python $MinPyMajor.$MinPyMinor+ from https://python.org (or run: winget install Python.Python.3.12), or set `$env:PYTHON to a $MinPyMajor.$MinPyMinor+ interpreter."
    if ($env:PYTHON) {
        # 用户用 $env:PYTHON 显式指定：尊重其选择，不擅自改用别的解释器，直接清晰报错。
        throw "PYTHON=$($env:PYTHON) points to Python $found, but chinalaw requires >= $MinPyMajor.$MinPyMinor. $guidance"
    }
    # 默认解释器太旧：自动探测系统里更新的解释器（py 启动器 + 版本化命令）。
    # 注意：PowerShell 会展平嵌套数组，故候选用字符串表示、调用时再按空格切分，
    # 避免 @(@('py','-3.13'), ...) 被拍平成一维字符串数组。
    $candidates = @(
        'py -3.13', 'py -3.12', 'py -3.11', 'py -3.10',
        'python3.13', 'python3.12', 'python3.11', 'python3.10'
    )
    foreach ($candStr in $candidates) {
        $cand = @($candStr -split '\s+')
        if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
        if (Test-PythonVersion -Python $cand) {
            $exe = Invoke-Python -Python $cand -PyArgs @('-c', 'import sys; print(sys.executable)') 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) {
                $exe = ("$exe").Trim()
                Write-Host "note: default python is $found (< $MinPyMajor.$MinPyMinor); using $exe"
                return $exe
            }
        }
    }
    throw "No Python >= $MinPyMajor.$MinPyMinor found (default python is $found). $guidance"
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function Test-PipAvailable {
    Param([Parameter(Mandatory = $true)][string]$Python)
    & $Python -m pip --version *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path $PythonBin)) {
    $BootstrapPython = Resolve-BootstrapPython -Current $BootstrapPython
    & $BootstrapPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "failed to create virtualenv with $BootstrapPython; wrappers will use PYTHONPATH fallback mode. Ensure a working Python $MinPyMajor.$MinPyMinor+ (with venv) is installed from https://python.org (or run: winget install Python.Python.3.12)"
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
        Write-Warning "editable install failed; wrapper will fall back to PYTHONPATH mode. Common cause: the venv's pip is too old for PEP 660 editable installs (error may masquerade as 'setup.py or setup.cfg not found'). Try: & `"$PythonBin`" -m pip install --upgrade pip; then rerun scripts\install-local.ps1"
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

    # Keep the batch file ASCII-only and free of absolute paths. The adjacent
    # UTF-8 PowerShell shim owns Unicode/%-bearing Python and repository paths;
    # cmd.exe expands %~dp0 once without re-expanding percent signs in its value.
    $cmdContent = @"
@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0$Name.ps1" %*
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
