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
# Log: <LogDir>\<LogName>_<yyyyMMdd>.txt (appended), ending with "TASK EXIT CODE: N". If LogDir can't be written
# (the SSD is unplugged or dropping, owner 2026-10-11), the log goes to FallbackLogDir on the internal drive instead,
# so the command still runs (stream-session --fallback-local then records there too).
param(
    [Parameter(Mandatory = $true)][string]$LogDir,
    [Parameter(Mandatory = $true)][string]$LogName,
    [Parameter(Mandatory = $true)][string]$NqbotArgs,
    [string]$Exe = "uv run nqbot",  # tests replace it with a stand-in program
    [string]$FallbackLogDir = (Join-Path $env:LOCALAPPDATA "nqbot\fallback\nqbot\logs")
)

function Test-Writable([string]$dir) {
    try {
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
        $probe = Join-Path $dir (".probe_" + [guid]::NewGuid().ToString("N"))
        [System.IO.File]::WriteAllText($probe, "x")
        Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
        return $true
    } catch { return $false }
}

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$note = ""
if (-not (Test-Writable $LogDir)) {
    $note = "log dir $LogDir not writable: logging to $FallbackLogDir`r`n"
    $LogDir = $FallbackLogDir
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}
$log = Join-Path $LogDir ("{0}_{1}.txt" -f $LogName, (Get-Date -Format yyyyMMdd))
if ($log.Contains(" ")) { throw "the log path may not contain spaces: $log" }
$utf8 = New-Object System.Text.UTF8Encoding $false

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"  # stdout and stderr lines stay in order in the log
# owner 2026-10-16: if the SSD (and this log) vanishes mid-run, nqbot carries on writing its output to a file of the
# same name in FallbackLogDir, and so does the exit-code line below
$env:NQBOT_TASK_LOG = $log
[System.IO.File]::AppendAllText($log, ("TASK START {0} ET: {1} {2}`r`n{3}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Exe, $NqbotArgs, $note), $utf8)
# A program path with a space (the PC's C:\Users\Prem Nadesan\..., 2026-10-30) is quoted for cmd.exe; /s makes cmd
# strip only the outer pair of quotes, so the quoted path survives. Start-Process passes the line as written
# (PowerShell 5.1's own native-call quoting mangles embedded quotes).
$cmdExe = $Exe
if ($Exe -match '^(?<prog>[^"]+?\.exe)(?<rest>(\s.*)?)$' -and $Matches.prog.Contains(" ")) {
    $cmdExe = '"' + $Matches.prog + '"' + $Matches.rest
}
$proc = Start-Process -FilePath cmd.exe -ArgumentList "/d /s /c `"$cmdExe $NqbotArgs >> $log 2>&1`"" `
    -NoNewWindow -Wait -PassThru
$code = $proc.ExitCode
$line = "TASK EXIT CODE: {0}`r`n" -f $code
try {
    [System.IO.File]::AppendAllText($log, $line, $utf8)
} catch {
    New-Item -ItemType Directory -Force -Path $FallbackLogDir -ErrorAction SilentlyContinue | Out-Null
    $alt = Join-Path $FallbackLogDir (Split-Path -Leaf $log)
    try { [System.IO.File]::AppendAllText($alt, "log $log not writable at the end`r`n" + $line, $utf8) } catch {}
}
exit $code
