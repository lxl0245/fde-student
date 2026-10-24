#!/usr/bin/env pwsh
# ---------------------------------------------------------------------------
# tag_week.ps1
# Create the weekly submission snapshot tag for this repository.
#
# Policy (see README.md -> "Weekly versions and snapshot policy"):
#   * Single linear `main`. No permanent per-week branches.
#   * Each week gets ONE annotated tag: wNN-<semver>, derived from the
#     product_version field in MANIFEST.json (e.g. "0.2.0-w1" -> w01-0.2.0).
#   * Revisions keep the same base name and add a suffix: -r1, -r2, ...
#
# This file is intentionally ASCII-only so it stays safe on Windows
# PowerShell 5.1. Pass Chinese release notes via -Message if needed.
#
# Usage:
#   .\tools\tag_week.ps1                       # create tag from MANIFEST.json
#   .\tools\tag_week.ps1 -WhatIf               # preview only
#   .\tools\tag_week.ps1 -Revision 1           # w02-0.5.4-r1
#   .\tools\tag_week.ps1 -Week 2 -Version 0.5.4
#   .\tools\tag_week.ps1 -Push                 # also push main + tags
# ---------------------------------------------------------------------------
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Week,
    [string]$Version,
    [string]$Revision,
    [string]$Message,
    [string]$Root,
    [switch]$Push,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'

function Fail([string]$text) {
    Write-Error $text
    exit 1
}

# --- 1. locate the repository root -----------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'git was not found on PATH.'
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Root)) {
        Fail 'Not inside a git repository. Run this script from the repo, or pass -Root.'
    }
}
$Root = $Root.Trim()
Push-Location $Root
try {
    Write-Host "Repo root : $Root" -ForegroundColor DarkGray

    # --- 2. refuse to tag a dirty working tree ------------------------------
    $dirty = (& git status --porcelain)
    if ($dirty) {
        if (-not $AllowDirty) {
            Write-Host 'Working tree is not clean. Commit (or stash) first:' -ForegroundColor Yellow
            $dirty | ForEach-Object { Write-Host "  $_" }
            Fail 'Re-run with -AllowDirty only if you know what you are doing.'
        }
        Write-Host 'Working tree is dirty but -AllowDirty was given.' -ForegroundColor Yellow
    }

    # --- 3. read MANIFEST.json ---------------------------------------------
    $manifestPath = Join-Path $Root 'MANIFEST.json'
    if (-not (Test-Path $manifestPath)) {
        Fail "MANIFEST.json not found at $manifestPath."
    }
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json

    $productVersion = [string]$manifest.product_version
    if ([string]::IsNullOrWhiteSpace($productVersion)) {
        Fail 'MANIFEST.json has no product_version field. Pass -Week and -Version instead.'
    }
    Write-Host "product_version : $productVersion" -ForegroundColor DarkGray

    # derive week + semver from e.g. "0.2.0-w1"
    $derivedWeek = $null
    $derivedSemver = $null
    if ($productVersion -match '^(?<semver>\d+(?:\.\d+)+)-(?<week>w\d+)$') {
        $derivedSemver = $Matches['semver']
        $weekNumber = [int]($Matches['week'].TrimStart('w'))
        $derivedWeek = '{0:d2}' -f $weekNumber
    }
    else {
        Write-Host "product_version '$productVersion' does not look like '<semver>-wN'; using it as-is." -ForegroundColor Yellow
        $derivedSemver = $productVersion
    }

    # explicit overrides win
    if (-not [string]::IsNullOrWhiteSpace($Week)) {
        $derivedWeek = ('{0:d2}' -f [int]($Week -replace '^w', ''))
    }
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $derivedSemver = $Version
    }
    if ([string]::IsNullOrWhiteSpace($derivedWeek)) {
        Fail 'Could not determine the week number. Pass -Week 2 (or use a product_version like 0.5.4-w2).'
    }

    # --- 4. build the tag name ---------------------------------------------
    $tagName = "w$derivedWeek-$derivedSemver"
    if (-not [string]::IsNullOrWhiteSpace($Revision)) {
        $revNumber = [int]($Revision -replace '^r', '')
        $tagName = "$tagName-r$revNumber"
    }

    # refuse to move / duplicate an existing tag
    $localExists = (& git rev-parse -q --verify "refs/tags/$tagName" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $localExists) {
        Fail "Tag '$tagName' already exists locally. Use -Revision <n> for a revision tag."
    }
    # Refuse to duplicate a remote tag. Network problems must not be mistaken
    # for "no such tag": only a successful probe that actually returns a ref
    # counts as a duplicate. Offline runs keep working on the local tag.
    $offline = $false
    $remoteProbe = $null
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $remoteProbe = (& git ls-remote --tags origin "refs/tags/$tagName" 2>$null)
    $probeExit = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference

    if ($probeExit -eq 0) {
        if ($remoteProbe) {
            Fail "Tag '$tagName' already exists on origin. Refusing to duplicate it."
        }
    }
    else {
        Write-Host 'Warning: could not reach origin, skipped the remote duplicate check.' -ForegroundColor Yellow
        Write-Host '         The tag is created locally only; push it later.' -ForegroundColor Yellow
        $offline = $true
    }

    # --- 5. default tag message --------------------------------------------
    if ([string]::IsNullOrWhiteSpace($Message)) {
        $stamp = Get-Date -Format 'yyyy-MM-dd'
        $parts = @("W$derivedWeek submit snapshot $stamp", "product $productVersion")
        foreach ($field in @('contract_version', 'fit_gap_contract_version', 'runtime_revision', 'package_revision')) {
            $prop = $manifest.PSObject.Properties.Match($field)
            if ($prop -and $prop.Count -gt 0) {
                $value = [string]$prop[0].Value
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    $parts += "$field $value"
                }
            }
        }
        $Message = ($parts -join '; ')
    }

    Write-Host ''
    Write-Host "Tag     : $tagName" -ForegroundColor Cyan
    Write-Host "Message : $Message" -ForegroundColor Cyan

    # --- 6. create the annotated tag ---------------------------------------
    $target = (& git rev-parse HEAD).Trim()
    if ($PSCmdlet.ShouldProcess($tagName, "git tag -a (on $target)")) {
        & git tag -a $tagName -m $Message
        if ($LASTEXITCODE -ne 0) { Fail 'git tag failed.' }
        Write-Host "Created annotated tag $tagName -> $target" -ForegroundColor Green
    }
    else {
        Write-Host 'Preview only, nothing was created.' -ForegroundColor Yellow
        exit 0
    }

    # --- 7. push ------------------------------------------------------------
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    $upstreamMissing = ($LASTEXITCODE -ne 0)

    if (-not $upstreamMissing) {
        $upstreamRef = (& git rev-parse -q --verify '@{u}' 2>$null)
        if ($upstreamRef) {
            # "N M" where N = commits only upstream has (behind), M = commits only local has (ahead)
            $counts = (& git rev-list --left-right --count '@{u}...HEAD').Trim() -split '\s+'
            if ($counts.Count -eq 2) {
                if ($counts[1] -ne '0') {
                    Write-Host "Note: HEAD is ahead of upstream by $($counts[1]) commit(s); pushing sends them too." -ForegroundColor Yellow
                }
                if ($counts[0] -ne '0') {
                    Write-Host "Warning: HEAD is behind upstream by $($counts[0]) commit(s); pull/rebase before tagging is safer." -ForegroundColor Yellow
                }
            }
        }
    }

    if ($Push -and $offline) {
        Write-Host ''
        Write-Host 'Skipping push: origin is unreachable. Nothing was lost locally.' -ForegroundColor Yellow
        $Push = $false
    }

    if ($Push) {
        $pushArgs = if ($branch -eq 'main') { @('push', '--atomic', 'origin', 'main', '--follow-tags') } else { @('push', 'origin', $tagName) }
        if ($PSCmdlet.ShouldProcess('origin', ($pushArgs -join ' '))) {
            & git @pushArgs
            if ($LASTEXITCODE -ne 0) {
                Fail 'git push failed (network or remote rejection). The local tag still exists; retry the push later.'
            }
            Write-Host 'Pushed. TODO: create the matching GitHub Release from this tag.' -ForegroundColor Green
        }
    }
    else {
        Write-Host ''
        Write-Host 'Next steps (tags are NOT pushed by a plain git push):' -ForegroundColor Yellow
        if ($branch -eq 'main') {
            Write-Host '  git push --atomic origin main --follow-tags'
        }
        else {
            Write-Host "  git push --atomic origin main --follow-tags   # after merging $branch into main"
            Write-Host "  git push origin $tagName"
        }
        Write-Host '  git ls-remote --tags origin                   # verify it reached GitHub'
        Write-Host '  then create a GitHub Release from this tag and paste this week evidence list.'
    }
}
finally {
    Pop-Location
}
