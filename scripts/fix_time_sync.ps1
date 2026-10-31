# Keep Windows time synced on this machine (owner 2026-10-31, SPEC section 11 Two machines, section 9 Clock).
# RUN FROM AN ADMIN POWERSHELL, on the PC and on the laptop (the live machine).
#
# Found on the PC 2026-10-31: the Windows Time service (w32time) was stopped, start type Manual with only a
# "domain joined" start trigger, so on a home PC it ran only on "Sync now" or Windows' own sync task, and the clock
# drifted (+535 ms after a restart) while "Set time automatically" read as on. Poll interval 0x8000 s (9 h).
#
# What it changes (prints the state before and after):
#   1. w32time start type Automatic (Delayed Start), started now
#   2. peers -Peers (default time.cloudflare.com, nqbot's ntp_server, then time.windows.com), each 0x9 (client
#      mode + special poll interval), polled every -PollSec (default 1024 s); then w32tm /resync /force
#      (2026-10-31: Windows clamps SpecialPollInterval to 2^MinPollInterval .. 2^MaxPollInterval, 1024 .. 32768 s on a
#      home PC, so the first version's 900 s ran every 1024 s; a -PollSec outside that range is refused. The 2048 s
#      "Poll Interval" of w32tm /query /status is the host's clock-discipline poll, not how often the peers are asked.)
#   3. a scheduled task "nqbot time resync" (SYSTEM, 2 min after every startup, up to 3 retries 1 min apart):
#      w32tm /resync /force, so the drift a restart brings is fixed at once
# -ShowOnly prints the current state and changes nothing (no admin needed; used by the tests).
param([string]$Peers = "time.cloudflare.com,0x9 time.windows.com,0x9", [int]$PollSec = 1024, [switch]$ShowOnly)

$ErrorActionPreference = "Stop"
$TaskName = "nqbot time resync"
$ClientKey = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient"
$ParamKey = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters"
$ConfigKey = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config"

function Get-PollRange {
    $g = Get-ItemProperty $ConfigKey -ErrorAction SilentlyContinue
    $min = if ($g.MinPollInterval) { [int]$g.MinPollInterval } else { 10 }
    $max = if ($g.MaxPollInterval) { [int]$g.MaxPollInterval } else { 15 }
    return @([math]::Pow(2, $min), [math]::Pow(2, $max))
}

function Show-TimeSync([string]$Title) {
    "=== $Title"
    $svc = Get-Service w32time
    $delayed = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time" -ErrorAction SilentlyContinue).DelayedAutostart
    "w32time: status=$($svc.Status) start=$($svc.StartType)$(if ($delayed -eq 1) { ' (delayed)' })"
    $p = Get-ItemProperty $ParamKey -ErrorAction SilentlyContinue
    "peers: $($p.NtpServer)  type=$($p.Type)"
    $c = Get-ItemProperty $ClientKey -ErrorAction SilentlyContinue
    $lo, $hi = Get-PollRange
    $eff = if ($c.SpecialPollInterval) { [math]::Min([math]::Max([double]$c.SpecialPollInterval, $lo), $hi) } else { $null }
    "poll interval (SpecialPollInterval): $($c.SpecialPollInterval) s, effective $eff s (Windows keeps it within $lo .. $hi s)"
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    "startup resync task: $(if ($t) { "$($t.State), runs as $($t.Principal.UserId)" } else { 'not registered' })"
    "w32tm /query /status:"
    $out = & w32tm /query /status 2>&1
    $out | ForEach-Object { "  $_" }
}

Show-TimeSync "before"
if ($ShowOnly) { exit 0 }
$lo, $hi = Get-PollRange
if ($PollSec -lt $lo -or $PollSec -gt $hi) {
    throw "-PollSec $PollSec is outside $lo .. $hi s, which Windows would clamp it to; nothing was changed"
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "run this from an admin PowerShell (right-click > Run as administrator); nothing was changed" }

"--- 1. w32time: Automatic (Delayed Start), started"
& sc.exe config w32time start= delayed-auto | Out-Null
if ($LASTEXITCODE -ne 0) { throw "sc.exe config w32time failed ($LASTEXITCODE)" }
Start-Service w32time

"--- 2. peers $Peers, poll every $PollSec s, resync"
& w32tm /config "/manualpeerlist:$Peers" /syncfromflags:manual /update | Out-Null
if ($LASTEXITCODE -ne 0) { throw "w32tm /config failed ($LASTEXITCODE)" }
Set-ItemProperty $ClientKey -Name SpecialPollInterval -Value $PollSec -Type DWord
& w32tm /config /update | Out-Null
Restart-Service w32time
Start-Sleep -Seconds 2
& w32tm /resync /force

"--- 3. scheduled task '$TaskName' (SYSTEM, 2 min after startup)"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT2M"  # let the network come up first
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\w32tm.exe" -Argument "/resync /force"
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $TaskName -Trigger $trigger -Action $action -Principal $principal `
    -Settings $settings -Description "nqbot (owner 2026-10-31): resync the clock after every startup" | Out-Null

Start-Sleep -Seconds 3
Show-TimeSync "after"
"check: uv run nqbot status (clock lines: Windows Time service running, offset within the limit)"
