# Create or move the build worktree (owner 2026-11-09, SPEC §13 Q-dryrun13 (1), CLAUDE.md rule 13). Normal PowerShell
# (no admin), on the build PC, from the main folder:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_worktree.ps1 -Commit <sha> [-Path C:\nqb\build]
#
# Builds and every long job run from this separate git worktree at a short path (clear of the Windows long-path
# limit), with its own uv environment, pinned to a commit whose full-suite pass is recorded (rule 14 (f)); coding
# continues in the main folder. Steps:
#   1. refuses while a job lock is held by a live process (the pin moves only between jobs: `nqbot build-pin can-move`);
#   2. creates the worktree (`git worktree add --detach <Path> <sha>`) or moves it (`git checkout --detach <sha>`,
#      refused if it has local changes to tracked files);
#   3. `uv sync --frozen` inside it (its own .venv); copies config\local.yaml and .env from the main folder (both
#      untracked, per machine; printed);
#   4. `uv run nqbot build-pin set` inside it: refuses unless that commit passed the full suite, then records
#      <root>\data\jobs\build_pin.json. From then on `nqbot run` / `resume` / `backup` / `queue add` refuse anywhere
#      else (`uv run nqbot build-pin clear` removes the pin). Re-register the PC tasks once so they start there
#      (admin PowerShell: scripts\register_tasks_pc.ps1; it reads the pin).
# -DryRun prints the steps without doing them.
param([Parameter(Mandatory = $true)][string]$Commit, [string]$Path = "C:\nqb\build",
      [string]$Repo = "", [string]$Uv = "", [switch]$DryRun)

$ErrorActionPreference = "Stop"
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }   # (PowerShell 5.1: $PSScriptRoot is empty in param)
if (-not $Uv) {
    $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { throw "uv not found on PATH: pass -Uv <full path to uv.exe>" }
    $Uv = $found.Source
}

function Invoke-Native([string]$What, [scriptblock]$Cmd) {
    # native tools write progress to stderr; PowerShell 5.1 would turn it into errors: judge by the exit code only
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & $Cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    $out | ForEach-Object { "  $_" }
    if ($code -ne 0) { throw "$What failed (exit $code)" }
}

$sha = (git -C $Repo rev-parse --verify "$Commit^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or -not $sha) { throw "not a commit in $Repo`: $Commit" }
"repo: $Repo"
"commit: $sha"
"worktree: $Path"
"uv: $Uv"
if ($DryRun) {
    "dry run: would check no job runs, then " + $(if (Test-Path $Path) { "checkout --detach $sha in $Path" }
        else { "git worktree add --detach $Path $sha" }) + ", uv sync --frozen, copy local.yaml/.env, build-pin set"
    exit 0
}

Push-Location $Repo
try { Invoke-Native "nqbot build-pin can-move" { & $Uv run nqbot build-pin can-move } } finally { Pop-Location }

if (Test-Path $Path) {
    $dirty = git -C $Path status --porcelain --untracked-files=no
    if ($dirty) { throw "the build worktree $Path has local changes to tracked files: it is never edited (rule 13)" }
    Invoke-Native "git checkout" { git -C $Path checkout --detach $sha }
} else {
    New-Item -ItemType Directory -Force (Split-Path -Parent $Path) | Out-Null
    Invoke-Native "git worktree add" { git -C $Repo worktree add --detach $Path $sha }
}
foreach ($rel in @("config\local.yaml", ".env")) {
    $src = Join-Path $Repo $rel
    if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $Path $rel) -Force
        "copied $rel"
    }
}
Push-Location $Path
try {
    Invoke-Native "uv sync" { & $Uv sync --frozen }
    Invoke-Native "nqbot build-pin set" { & $Uv run nqbot build-pin set }
} finally { Pop-Location }
"done: long jobs now run from $Path (cd $Path; uv run nqbot run <job> --until HH:MM)"
