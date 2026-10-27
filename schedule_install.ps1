# Creates a Windows scheduled task that runs the READ-ONLY routine (sync, export, SEO audit, report).
# Edit these two lines if you want another day/time:
$Day  = 'Saturday'
$Time = '09:00'

$task     = 'ZafaranAgentRoutine'
$bat      = Join-Path $PSScriptRoot 'run_routine.bat'
$action   = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $PSScriptRoot
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
# StartWhenAvailable: if the PC was off at that time, run as soon as it is on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Description 'Zafaran Agent read-only routine' -Force | Out-Null
Write-Host "Scheduled: every $Day at $Time (runs later if the PC was off). Task name: $task"
Write-Host "Report appears in the reports folder as routine_latest.md"
