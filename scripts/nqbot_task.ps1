# Run one nqbot command from a Windows scheduled task (owner 2026-10-10).
#
# The task's exit code is nqbot's own exit code (0 = finished cleanly). Under `powershell -Command`, redirecting a
# native program's stderr wraps every log line in an error record and the task ends with 1 even when the program
# exited 0. Here cmd.exe does the redirect, so PowerShell never sees the program's output, and both streams land in
# one UTF-8 log file in the order they were written.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\nqbot_task.ps1 -LogDir D:\nqbot\logs `
#       -LogName stream_session -NqbotArgs "step7 stream-session --maps NDXP,QQQ,SPY"
#
# Log: <LogDir>\<LogName>_<yyyyMMdd>.txt (appended), ending with "TASK EXIT CODE: N".
param(
    [Parameter(Mandatory = $true)][string]$LogDir,
    [Parameter(Mandatory = $true)][string]$LogName,
    [Parameter(Mandatory = $true)][string]$NqbotArgs,
    [string]$Exe = "uv run nqbot"  # tests replace it with a stand-in program
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$log = Join-Path $LogDir ("{0}_{1}.txt" -f $LogName, (Get-Date -Format yyyyMMdd))
if ($log.Contains(" ")) { throw "the log path may not contain spaces: $log" }
$utf8 = New-Object System.Text.UTF8Encoding $false

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"  # stdout and stderr lines stay in order in the log
[System.IO.File]::AppendAllText($log, ("TASK START {0} ET: {1} {2}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Exe, $NqbotArgs), $utf8)
& cmd.exe /d /c "$Exe $NqbotArgs >> $log 2>&1"
$code = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, ("TASK EXIT CODE: {0}`r`n" -f $code), $utf8)
exit $code
