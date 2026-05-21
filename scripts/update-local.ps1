Param(
    [switch]$SyncFixtures,
    [switch]$NoSkills,
    [switch]$NoDoctor
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BinDir = if ($env:CHINALAW_BIN_DIR) { $env:CHINALAW_BIN_DIR } else { Join-Path $HOME ".local\bin" }
$Chinalaw = Join-Path $BinDir "chinalaw.cmd"

Push-Location $RepoRoot
try {
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -eq 0) {
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            throw "git pull --ff-only failed"
        }
    }
}
finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot "install-local.ps1")

if (-not $NoSkills) {
    & (Join-Path $PSScriptRoot "install-skills.ps1")
}

if ($SyncFixtures) {
    & $Chinalaw sync --fixtures --format md
}

if (-not $NoDoctor) {
    & $Chinalaw doctor --format md
}
