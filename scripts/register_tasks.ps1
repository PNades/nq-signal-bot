# Register the nqbot scheduled tasks for the coming sessions (owner 2026-10-14). RUN FROM AN ADMIN POWERSHELL.
#
# Why admin: a task that runs "whether the user is logged on or not" (S4U logon, no password stored) can only be
# registered elevated, and the existing nqbot tasks were registered elevated (owner BUILTIN\Administrators, the user
# has Read only), so a normal shell can't change or delete them.
#
# Why S4U: on 10-14 Windows Update restarted the laptop at 04:50 ET and Windows stayed at the sign-in screen; an
# Interactive task can't start without a signed-in user, so the 09:27 recording never ran. An S4U task runs as you in
# its own session even at the sign-in screen: your profile (LOCALAPPDATA, uv's cache), the SSD (drive letters are
# global) and the internet are all there; it has no access to network shares or saved Windows credentials, which
# nqbot doesn't use (keys come from .env). uv is called by its full path, so PATH doesn't matter.
#
# What it does: removes the finished one-off nqbot tasks, then registers
#   nqbot preflight             Mon-Fri 08:20 ET  (restart pending / update pause / SSD / AC; exit 5 = warnings)
#   nqbot late-prints 10-15     Thu 10-15 08:40   (late-print morning #5)
#   nqbot recording 10-15       Thu 10-15 09:27   (stream-session NDXP,QQQ,SPY --fallback-local; the only recording task)
#   nqbot sigma check           09:05 and 12:00 on 10-16, 10-19, 10-20 (moved off the 10-15 recording day)
# Every task: wake the computer, start when available if missed, run on battery, one instance at a time.
param([string]$Repo = "C:\Users\premn\code\nq-signal-bot", [string]$LogDir = "D:\nqbot\logs",
      [string]$Uv = "C:\Users\premn\.local\bin\uv.exe")

$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "run this from an admin PowerShell (right-click > Run as administrator)" }
if (-not (Test-Path $Uv)) { throw "uv not found at $Uv (pass -Uv <path>)" }

$old = "nqbot NDXP session recording", "nqbot late-prints-morning 10-12", "nqbot tempest-finality 10-12",
       "nqbot dp-start re-probe reminder", "nqbot sigma check",
       "nqbot preflight", "nqbot late-prints 10-15", "nqbot recording 10-15"
foreach ($n in $old) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $n -Confirm:$false
        "removed: $n"
    }
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$wrapper = Join-Path $Repo "scripts\nqbot_task.ps1"

function Add-NqbotTask([string]$Name, [string]$LogName, [string]$NqbotArgs, $Triggers, [int]$Hours) {
    $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`" -LogDir $LogDir " +
           "-LogName $LogName -Exe `"$Uv run nqbot`" -NqbotArgs `"$NqbotArgs`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Repo
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours $Hours)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers -Principal $principal `
        -Settings $settings -Description "nqbot (owner 2026-10-14): $NqbotArgs" | Out-Null
    $t = Get-ScheduledTask -TaskName $Name
    "registered: $Name  logon=$($t.Principal.LogonType)  next=$(($t | Get-ScheduledTaskInfo).NextRunTime)"
}

$weekdays = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "08:20"
Add-NqbotTask "nqbot preflight" "preflight" "preflight" $weekdays 1
Add-NqbotTask "nqbot late-prints 10-15" "late_prints_morning" "step7 late-prints-morning" `
    (New-ScheduledTaskTrigger -Once -At "2026-10-15 08:40") 1
Add-NqbotTask "nqbot recording 10-15" "stream_session" "step7 stream-session --maps NDXP,QQQ,SPY --fallback-local" `
    (New-ScheduledTaskTrigger -Once -At "2026-10-15 09:27") 8
$sigma = foreach ($d in "2026-10-16", "2026-10-19", "2026-10-20") {
    New-ScheduledTaskTrigger -Once -At "$d 09:05"; New-ScheduledTaskTrigger -Once -At "$d 12:00" }
Add-NqbotTask "nqbot sigma check" "sigma_check" "step7 sigma-check" $sigma 1

"--- recording tasks firing on 2026-10-15 (must be exactly one):"
Get-ScheduledTask | Where-Object { $_.TaskName -like "nqbot*" -and $_.Actions.Arguments -match "stream-session" } |
    ForEach-Object { $n = $_.TaskName; $_.Triggers | Where-Object { $_.StartBoundary -like "2026-10-15*" } |
        ForEach-Object { "  $n at $($_.StartBoundary)" } }
