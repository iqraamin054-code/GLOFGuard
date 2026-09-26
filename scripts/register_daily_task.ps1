param(
    [string]$TaskName = 'GLOFGuardDailyRefresh',
    [datetime]$At = '03:00'
)

$ErrorActionPreference = 'Stop'
$Runner = (Resolve-Path (Join-Path $PSScriptRoot 'run_daily_refresh.ps1')).Path
$Argument = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $Argument
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$TaskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Refresh official environmental observations and GLOF research indicators.' `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $TaskSettings

Write-Output "Registered $TaskName to run daily at $($At.ToString('HH:mm'))."
