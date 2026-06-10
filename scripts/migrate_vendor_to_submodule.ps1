# Converts vendor/lengrvis-code into a git submodule of this repository.
#
# Current state: vendor/lengrvis-code has already been exported to its own
# standalone git repository in place (it contains a .git directory) and is
# untracked/ignored by the main repository (.gitignore). To finish the
# submodule conversion once a remote exists:
#
# Prerequisites:
#   1. Create an empty remote repository, then push the standalone repo:
#        cd vendor\lengrvis-code
#        git remote add origin <vendor-repo-url> && git push -u origin main
#   2. Run this script from a CLEAN worktree (commit or stash everything else).
#
# Usage:
#   .\scripts\migrate_vendor_to_submodule.ps1 -VendorRemoteUrl git@github.com:<owner>/lengrvis-code.git
#
# After the migration:
#   - Collaborators run: git submodule update --init vendor/lengrvis-code
#   - CI that needs vendor content must add: submodules: true (actions/checkout)
#   - Consider keeping vendor/lengrvis-code out of editor indexing (see
#     .cursorindexingignore) so its AGENTS.md/CLAUDE.md rules and file volume
#     stop weighing on the main repo's tooling.

param(
    [Parameter(Mandatory = $true)]
    [string]$VendorRemoteUrl,
    [string]$VendorPath = "vendor/lengrvis-code",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$status = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw "git status failed; is this a git repository?" }
if ($status) {
    throw "Worktree is not clean. Commit or stash all changes before migrating vendor/lengrvis-code."
}

if (-not (Test-Path -LiteralPath $VendorPath -PathType Container)) {
    throw "Vendor directory not found: $VendorPath"
}

Write-Host "Verifying the vendor remote is reachable..."
git ls-remote --exit-code $VendorRemoteUrl $Branch | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Cannot reach $VendorRemoteUrl (branch $Branch). Push the exported vendor repo first; see the header of this script."
}

Write-Host "Removing $VendorPath from the index (files stay on disk until submodule checkout)..."
git rm -r -q --cached $VendorPath
if ($LASTEXITCODE -ne 0) { throw "git rm --cached failed." }

$backupPath = "$VendorPath-pre-submodule-backup"
Write-Host "Moving the working copy aside to $backupPath..."
Move-Item -LiteralPath $VendorPath -Destination $backupPath -Force

Write-Host "Adding submodule $VendorRemoteUrl at $VendorPath..."
git submodule add -b $Branch $VendorRemoteUrl $VendorPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "Submodule add failed; restoring the original directory." -ForegroundColor Yellow
    Move-Item -LiteralPath $backupPath -Destination $VendorPath -Force
    git reset -q
    throw "git submodule add failed; repository restored to the pre-migration state."
}

git add .gitmodules $VendorPath
git commit -m "chore: convert vendor/lengrvis-code to a git submodule"
if ($LASTEXITCODE -ne 0) { throw "Commit failed; inspect 'git status' manually." }

Write-Host ""
Write-Host "Done. Review the commit, then delete the backup once satisfied:" -ForegroundColor Green
Write-Host "  Remove-Item -Recurse -Force `"$backupPath`""
Write-Host "Collaborators: git submodule update --init $VendorPath"
