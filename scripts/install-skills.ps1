Param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [string[]]$Target
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SkillsDir = Join-Path $RepoRoot ".claude\skills"
$InstallMarker = ".chinalaw-cli-install"

if (-not (Test-Path $SkillsDir)) {
    throw "$SkillsDir not found; run from a chinalaw-cli checkout"
}

if (-not $Target -or $Target.Count -eq 0) {
    $openCodeDir = if ($env:APPDATA) {
        Join-Path $env:APPDATA "opencode\skills"
    }
    else {
        Join-Path $HOME ".config\opencode\skills"
    }
    $Target = @(
        (Join-Path $HOME ".claude\skills"),
        (Join-Path $HOME ".agents\skills"),
        $openCodeDir
    )
}

function Invoke-Step {
    Param([scriptblock]$Step, [string]$Message)
    if ($DryRun) {
        Write-Host "[dry-run] $Message"
    }
    else {
        & $Step
    }
}

function Test-ManagedSkill {
    Param([string]$Path, [string]$Name)
    $marker = Join-Path $Path $InstallMarker
    if (-not (Test-Path $marker)) {
        return $false
    }
    $text = Get-Content -Raw -Path $marker
    return $text.Contains("managed_by=chinalaw-cli") -and
        $text.Contains("installer=scripts/install-skills.ps1") -and
        $text.Contains("skill=$Name")
}

$skillNames = Get-ChildItem -Path $SkillsDir -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "SKILL.md") } |
    Select-Object -ExpandProperty Name

if (-not $skillNames) {
    throw "no skills with SKILL.md found under $SkillsDir"
}

$mode = if ($Uninstall) { "uninstall" } else { "copy" }
Write-Host "chinalaw-cli skills: $mode"
Write-Host "  source : $SkillsDir"
foreach ($targetDir in $Target) {
    Write-Host "  target : $targetDir"
}
Write-Host "  skills : $($skillNames -join ' ')"
Write-Host ""

foreach ($targetDir in $Target) {
    Invoke-Step { New-Item -ItemType Directory -Force -Path $targetDir | Out-Null } "mkdir $targetDir"

    foreach ($name in $skillNames) {
        $src = Join-Path $SkillsDir $name
        $dst = Join-Path $targetDir $name

        if ($Uninstall) {
            if ((Test-Path $dst) -and (Test-ManagedSkill -Path $dst -Name $name)) {
                Invoke-Step { Remove-Item -Recurse -Force -Path $dst } "remove $dst"
                Write-Host "removed copy: $dst"
            }
            elseif (Test-Path $dst) {
                Write-Host "skip (foreign dir): $dst"
            }
            else {
                Write-Host "skip (not present): $dst"
            }
            continue
        }

        if (Test-Path $dst) {
            if (Test-ManagedSkill -Path $dst -Name $name) {
                Invoke-Step { Remove-Item -Recurse -Force -Path $dst } "refresh managed copy $dst"
            }
            else {
                Write-Warning "existing directory is not managed by chinalaw-cli; skipping: $dst"
                continue
            }
        }

        Invoke-Step { Copy-Item -Recurse -Path $src -Destination $dst } "copy $src -> $dst"
        $markerText = @"
managed_by=chinalaw-cli
installer=scripts/install-skills.ps1
skill=$name
source=$src
"@
        Invoke-Step { Set-Content -Path (Join-Path $dst $InstallMarker) -Value $markerText -Encoding UTF8 } "write marker $dst\$InstallMarker"
        Write-Host "copied: $src -> $dst"
    }
}

Write-Host ""
Write-Host "done."
