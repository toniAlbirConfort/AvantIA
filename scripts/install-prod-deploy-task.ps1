[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$watcherPath = Join-Path $PSScriptRoot 'deploy-prod-watch.ps1'
if (-not (Test-Path -LiteralPath $watcherPath)) {
    throw "No se encuentra el monitor de despliegue: $watcherPath"
}

$taskName = 'AvantIA Production Auto Deploy'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$action = New-ScheduledTaskAction `
    -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$watcherPath`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Comprueba origin/main y despliega AvantIA en Docker Compose desde la sesión interactiva.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Output "Tarea '$taskName' instalada para $identity y puesta en marcha."
Write-Output "Registro: $env:LOCALAPPDATA\AvantIA\deploy-watch.log"
