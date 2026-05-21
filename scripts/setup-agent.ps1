Param(
    [switch]$SyncFixtures,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$BinDir = if ($env:CHINALAW_BIN_DIR) { $env:CHINALAW_BIN_DIR } else { Join-Path $HOME ".local\bin" }
$Chinalaw = Join-Path $BinDir "chinalaw.cmd"

& (Join-Path $PSScriptRoot "install-local.ps1")
& (Join-Path $PSScriptRoot "install-skills.ps1") -DryRun:$DryRun

if ($SyncFixtures) {
    & $Chinalaw sync --fixtures --format md
}

& $Chinalaw doctor --format md
