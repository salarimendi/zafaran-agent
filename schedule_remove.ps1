Unregister-ScheduledTask -TaskName 'ZafaranAgentRoutine' -Confirm:$false -ErrorAction SilentlyContinue
Write-Host 'Scheduled task removed (if it existed).'
