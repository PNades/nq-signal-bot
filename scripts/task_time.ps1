# Dot-sourced by register_tasks.ps1 and register_tasks_pc.ps1 (owner 2026-11-02).
#
# New-ScheduledTaskTrigger -At stores the start as a UTC instant ("...Z": "Synchronize across time zones" on), so a
# daily or weekly task registered for 07:35 under EDT fires at 06:35 after the clocks go back (and the reverse in
# March): seen on the PC on 2026-11-02 (preflight 07:40 -> next run 06:40). A start without an offset is local wall
# time, which Task Scheduler keeps at 07:35 ET all year. A trigger without a start (at startup) is left as it is.
function ConvertTo-LocalTrigger {
    param([Parameter(ValueFromPipeline = $true)]$Trigger)
    process {
        if ($Trigger.StartBoundary) {
            $Trigger.StartBoundary = ([datetime]$Trigger.StartBoundary).ToString("yyyy-MM-dd'T'HH:mm:ss")
        }
        $Trigger
    }
}
