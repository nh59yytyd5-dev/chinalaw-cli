Param(
    [switch]$SyncFixtures,
    [switch]$NoSyncFixtures,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($SyncFixtures -and $NoSyncFixtures) {
    throw "Cannot combine -SyncFixtures with -NoSyncFixtures"
}

$BinDir = if ($env:CHINALAW_BIN_DIR) { $env:CHINALAW_BIN_DIR } else { Join-Path $HOME ".local\bin" }
$Chinalaw = Join-Path $BinDir "chinalaw.cmd"

# 判定是否需要为「新用户」加载 fixtures：默认数据库文件不存在/为空即视为首次运行；
# 文件已存在时进一步用 status 判断是否 0 部法规。解析失败则保守视为非空，不覆盖数据。
function Test-DbIsEmpty {
    $db = if ($env:CHINALAW_DB) { $env:CHINALAW_DB } else { Join-Path $HOME ".chinalaw\chinalaw.db" }
    if (-not (Test-Path $db) -or (Get-Item $db).Length -eq 0) { return $true }
    try {
        $json = & $Chinalaw status --format json 2>$null | Out-String
        if ($LASTEXITCODE -ne 0 -or -not $json) { return $false }
        return [int]((ConvertFrom-Json $json).laws) -eq 0
    }
    catch { return $false }
}

& (Join-Path $PSScriptRoot "install-local.ps1")
& (Join-Path $PSScriptRoot "install-skills.ps1") -DryRun:$DryRun

if ($SyncFixtures) {
    & $Chinalaw sync --fixtures --format md
}
elseif ($NoSyncFixtures) {
    # 用户显式跳过 fixtures
}
elseif (Test-DbIsEmpty) {
    Write-Host "==> first run: database is empty, loading public fixtures (use -NoSyncFixtures to skip)"
    & $Chinalaw sync --fixtures --format md
}

& $Chinalaw doctor --format md
