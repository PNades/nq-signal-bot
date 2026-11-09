# Register the nqbot scheduled tasks on the home PC, the build machine (owner 2026-10-28, SPEC §11 Two machines,
# Overnight jobs). RUN FROM AN ADMIN POWERSHELL ON THE PC, never on the laptop.
#
# S4U logon (as on the laptop, register_tasks.ps1): the tasks run as you at the sign-in screen after a Windows
# Update restart or a power cut, with your profile and the SSD; no password stored. uv is called by its full path.
#
# Tasks (each: wake the computer, start when available if missed, one instance at a time):
#   nqbot resume       at startup (+2 min), daily 07:35 and every hour: `nqbot resume` runs the queued jobs
#                      (`nqbot queue add <job>`) from their last chunk; no time limit (it ends itself at --until 07:30,
#                      and a task time limit would kill a chunk mid-call); a second copy is refused by the task
#                      (IgnoreNew) and by the job lock. The first resume after a restart re-reads the last 2 days.
#   nqbot preflight    daily 07:40: restart pending, update pause, SSD, clock (time zone, sync, NTP offset), SSD drops
#   nqbot status push  every hour: `nqbot status` (+ queue, clock, latest preflight) to <root>\logs\status_<host>.txt
#                      and with rclone to Google Drive "NQ bot backup/status/" (the Drive app on the phone)
#
# Refuses if the repo's .env holds any TP_WEBHOOK_* line: the PC never trades (SPEC §11), so it must not be able to.
#
# uv (owner 2026-10-30): -Uv <path> if given, else the uv this PowerShell finds on PATH ((Get-Command uv).Source; on
# the PC winget's ...\WinGet\Links\uv.exe); the path used is printed first. -ResolveUvOnly prints it and stops
# (no admin needed; used by the tests).
#
# Times (owner 2026-11-02): every trigger is stored as local wall time (scripts/task_time.ps1), so 07:35 stays 07:35
# ET across the daylight-saving switches. -ShowTriggersOnly prints each task's triggers and stops (no admin needed;
# used by the tests). Tasks registered before 2026-11-02 were anchored to UTC: run this script again once.
#
# Repo (owner 2026-11-09, SPEC §13 Q-dryrun13 (1)): -Repo if given, else the build worktree recorded in the build pin
# (-PinFile, <root>\data\jobs\build_pin.json, written by scripts\build_worktree.ps1) when it is this machine's, else
# the main folder; the repo used is printed. -ShowRepoOnly prints it and stops (no admin needed; used by the tests).
param([string]$Repo = "", [string]$LogDir = "D:\nqbot\logs", [string]$PinFile = "D:\nqbot\data\jobs\build_pin.json",
      [string]$Uv = "", [switch]$ResolveUvOnly, [switch]$ShowTriggersOnly, [switch]$ShowRepoOnly)

$ErrorActionPreference = "Stop"
if ($Repo) {
    $repoNote = "from -Repo"
} elseif ((Test-Path -LiteralPath $PinFile) -and
          (($pin = Get-Content -LiteralPath $PinFile -Raw | ConvertFrom-Json).host -eq $env:COMPUTERNAME)) {
    $Repo = $pin.path
    $repoNote = "the build worktree pinned at $($pin.commit.Substring(0, 7)), $PinFile"
} else {
    $Repo = Join-Path $env:USERPROFILE "code\nq-signal-bot"
    $repoNote = "the main folder: no build pin for this machine"
}
if ($ShowRepoOnly) { "repo: $Repo ($repoNote)"; exit 0 }
if ($Uv) {
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) { throw "uv not found at -Uv $Uv" }
    "uv: $Uv (from -Uv)"
} else {
    $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        throw ("uv not found on this PowerShell's PATH: install it (winget install astral-sh.uv), open a new admin " +
               "PowerShell, or pass -Uv <full path to uv.exe>")
    }
    $Uv = $found.Source
    "uv: $Uv (found on PATH; pass -Uv <path> to use another)"
}
if ($ResolveUvOnly) { exit 0 }
"repo: $Repo ($repoNote)"
. (Join-Path $PSScriptRoot "task_time.ps1")

function Hourly([int]$Minute) {  # every hour from the next HH:<Minute>, indefinitely
    $start = (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes($Minute)
    New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1)
}

$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = "PT2M"  # let the SSD mount first (the wrapper also waits up to 30 s for it)
$triggers = [ordered]@{
    "nqbot resume"      = @($boot, (New-ScheduledTaskTrigger -Daily -At "07:35"), (Hourly 5)) | ConvertTo-LocalTrigger
    "nqbot preflight"   = @(New-ScheduledTaskTrigger -Daily -At "07:40") | ConvertTo-LocalTrigger
    "nqbot status push" = @(Hourly 15) | ConvertTo-LocalTrigger
}
if ($ShowTriggersOnly) {
    foreach ($name in $triggers.Keys) {
        foreach ($t in $triggers[$name]) {
            "$name|$($t.CimClass.CimClassName)|$($t.StartBoundary)|$($t.Repetition.Interval)"
        }
    }
    exit 0
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "run this from an admin PowerShell (right-click > Run as administrator)" }
if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) { throw "repo not found at $Repo (pass -Repo <path>)" }
$drive = Split-Path -Qualifier $LogDir
if (-not (Test-Path (Join-Path "$drive\" ".nqbot_drive"))) { throw "the SSD marker isn't on $drive (is the SSD D:?)" }
$envFile = Join-Path $Repo ".env"
if ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*(export\s+)?TP_WEBHOOK_' -Quiet)) {
    throw ".env holds TP_WEBHOOK_* lines: remove them on the PC (it never trades), then run this again"
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$wrapper = Join-Path $Repo "scripts\nqbot_task.ps1"

function Add-NqbotTask([string]$Name, [string]$LogName, [string]$NqbotArgs, $Triggers, [TimeSpan]$Limit) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        "replaced: $Name"
    }
    $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`" -LogDir $LogDir " +
           "-LogName $LogName -Exe `"$Uv run nqbot`" -NqbotArgs `"$NqbotArgs`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Repo
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit $Limit
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers -Principal $principal `
        -Settings $settings -Description "nqbot on the build PC (owner 2026-10-28): $NqbotArgs" | Out-Null
    $t = Get-ScheduledTask -TaskName $Name
    "registered: $Name  logon=$($t.Principal.LogonType)  next=$(($t | Get-ScheduledTaskInfo).NextRunTime)"
}

Add-NqbotTask "nqbot resume" "resume" "resume" $triggers["nqbot resume"] ([TimeSpan]::Zero)
Add-NqbotTask "nqbot preflight" "preflight" "preflight" $triggers["nqbot preflight"] (New-TimeSpan -Hours 1)
Add-NqbotTask "nqbot status push" "status_push" "status-push" $triggers["nqbot status push"] (New-TimeSpan -Minutes 10)

"--- nqbot tasks on this machine:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "nqbot*" } | ForEach-Object {
    "  $($_.TaskName)  state=$($_.State)  logon=$($_.Principal.LogonType)  next=$(($_ | Get-ScheduledTaskInfo).NextRunTime)"
    foreach ($t in $_.Triggers) { if ($t.StartBoundary) { "    start $($t.StartBoundary)" } } }
