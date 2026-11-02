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
#   nqbot recording 10-19       Mon 10-19 09:27   (stream-session NDXP,QQQ,SPY --fallback-local; the only recording task;
#                                                  replaces 10-16's, which stopped at 13:55 when the Wi-Fi went out)
#   nqbot sigma check           09:05 and 12:00 on 10-20, 10-21, 10-22 (sessions with no recording; 10-19 moved to 10-22)
#   nqbot stream-drops 10-16    Mon 10-19 08:50   (one history window vs the 10-16 recording, 25 credits; §13 Q-mapstale)
#   nqbot stream-drops 10-19    Tue 10-20 08:50   (the same for the 10-19 recording)
# Owner 2026-10-17: replaces the 10-16 tasks (late-prints, recording) and the 10-19 sigma runs.
# Every task: wake the computer, start when available if missed, run on battery, one instance at a time.
# Times (owner 2026-11-02): every trigger is stored as local wall time (scripts/task_time.ps1), so 08:20 stays 08:20
# ET across the daylight-saving switches (New-ScheduledTaskTrigger alone anchors it to UTC: an hour off after a
# switch). -ShowTriggersOnly prints each task's triggers and stops (no admin needed; used by the tests).
param([string]$Repo = "C:\Users\premn\code\nq-signal-bot", [string]$LogDir = "D:\nqbot\logs",
      [string]$Uv = "C:\Users\premn\.local\bin\uv.exe", [switch]$ShowTriggersOnly)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "task_time.ps1")
$triggers = [ordered]@{
    "nqbot preflight"          = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, `
                                       Friday -At "08:20") | ConvertTo-LocalTrigger
    "nqbot recording 10-19"    = @(New-ScheduledTaskTrigger -Once -At "2026-10-19 09:27") | ConvertTo-LocalTrigger
    "nqbot sigma check"        = @(foreach ($d in "2026-10-20", "2026-10-21", "2026-10-22") {
                                       New-ScheduledTaskTrigger -Once -At "$d 09:05"
                                       New-ScheduledTaskTrigger -Once -At "$d 12:00" }) | ConvertTo-LocalTrigger
    "nqbot stream-drops 10-16" = @(New-ScheduledTaskTrigger -Once -At "2026-10-19 08:50") | ConvertTo-LocalTrigger
    "nqbot stream-drops 10-19" = @(New-ScheduledTaskTrigger -Once -At "2026-10-20 08:50") | ConvertTo-LocalTrigger
}
if ($ShowTriggersOnly) {
    foreach ($name in $triggers.Keys) {
        foreach ($t in $triggers[$name]) { "$name|$($t.CimClass.CimClassName)|$($t.StartBoundary)" }
    }
    exit 0
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "run this from an admin PowerShell (right-click > Run as administrator)" }
if (-not (Test-Path $Uv)) { throw "uv not found at $Uv (pass -Uv <path>)" }

$old = "nqbot NDXP session recording", "nqbot late-prints-morning 10-12", "nqbot tempest-finality 10-12",
       "nqbot dp-start re-probe reminder", "nqbot sigma check",
       "nqbot preflight", "nqbot late-prints 10-15", "nqbot recording 10-15", "nqbot stream-drops 10-15",
       "nqbot late-prints 10-16", "nqbot recording 10-16", "nqbot stream-drops 10-16",
       "nqbot recording 10-19", "nqbot stream-drops 10-19"
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

Add-NqbotTask "nqbot preflight" "preflight" "preflight" $triggers["nqbot preflight"] 1
Add-NqbotTask "nqbot recording 10-19" "stream_session" "step7 stream-session --maps NDXP,QQQ,SPY --fallback-local" `
    $triggers["nqbot recording 10-19"] 8
Add-NqbotTask "nqbot sigma check" "sigma_check" "step7 sigma-check" $triggers["nqbot sigma check"] 1
Add-NqbotTask "nqbot stream-drops 10-16" "stream_drops" "step7 stream-drops --session 2026-10-16" `
    $triggers["nqbot stream-drops 10-16"] 1
Add-NqbotTask "nqbot stream-drops 10-19" "stream_drops" "step7 stream-drops --session 2026-10-19" `
    $triggers["nqbot stream-drops 10-19"] 1

"--- recording tasks firing on 2026-10-19 (must be exactly one):"
Get-ScheduledTask | Where-Object { $_.TaskName -like "nqbot*" -and $_.Actions.Arguments -match "stream-session" } |
    ForEach-Object { $n = $_.TaskName; $_.Triggers | Where-Object { $_.StartBoundary -like "2026-10-19*" } |
        ForEach-Object { "  $n at $($_.StartBoundary)" } }
"--- sigma check runs (none on a recording day):"
(Get-ScheduledTask -TaskName "nqbot sigma check").Triggers | ForEach-Object { "  $($_.StartBoundary)" }
