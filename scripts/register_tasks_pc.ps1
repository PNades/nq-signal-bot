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
param([string]$Repo = (Join-Path $env:USERPROFILE "code\nq-signal-bot"), [string]$LogDir = "D:\nqbot\logs",
      [string]$Uv = (Join-Path $env:USERPROFILE ".local\bin\uv.exe"))

$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "run this from an admin PowerShell (right-click > Run as administrator)" }
if (-not (Test-Path $Uv)) { throw "uv not found at $Uv (pass -Uv <path>)" }
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

function Hourly([int]$Minute) {  # every hour from the next HH:<Minute>, indefinitely
    $start = (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes($Minute)
    New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1)
}

$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = "PT2M"  # let the SSD mount first (the wrapper also waits up to 30 s for it)
Add-NqbotTask "nqbot resume" "resume" "resume" @($boot, (New-ScheduledTaskTrigger -Daily -At "07:35"), (Hourly 5)) `
    ([TimeSpan]::Zero)
Add-NqbotTask "nqbot preflight" "preflight" "preflight" (New-ScheduledTaskTrigger -Daily -At "07:40") (New-TimeSpan -Hours 1)
Add-NqbotTask "nqbot status push" "status_push" "status-push" (Hourly 15) (New-TimeSpan -Minutes 10)

"--- nqbot tasks on this machine:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "nqbot*" } | ForEach-Object {
    "  $($_.TaskName)  state=$($_.State)  logon=$($_.Principal.LogonType)  next=$(($_ | Get-ScheduledTaskInfo).NextRunTime)" }
