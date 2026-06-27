# Registers the supervisor watchdog as a Windows Scheduled Task running every 5 min.
# This keeps home-stack-supervisor.ps1 alive across screen-lock and sleep — the most
# common cause of the hidden supervisor/tunnel/cloudflared dying (visible bot window
# survives sleep; hidden processes do not).
#
# Run once (as the user who runs the bot):
#   powershell -ExecutionPolicy Bypass -File scripts\register-supervisor-watchdog.ps1
#
# To remove:  Unregister-ScheduledTask -TaskName DoxedSupervisorWatchdog -Confirm:$false
param([string]$TaskName = "DoxedSupervisorWatchdog")

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$watchdog = Join-Path $scriptDir "home-stack-supervisor-watchdog.ps1"
if (-not (Test-Path $watchdog)) { throw ("watchdog not found: " + $watchdog) }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchdog + '" -Quiet')

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -DontStopOnIdleEnd -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + "\" + $env:USERNAME) -LogonType Interactive -RunLevel Limited

try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop | Out-Null } catch { }

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Relaunches the Doxed home-stack supervisor every 5 min if it has died (survives lock/sleep)." | Out-Null

Write-Host ("Registered scheduled task '" + $TaskName + "' - watchdog runs every 5 min.") -ForegroundColor Green
Write-Host ("Watchdog: " + $watchdog)
Write-Host ("Remove with: Unregister-ScheduledTask -TaskName " + $TaskName + " -Confirm:$false")
