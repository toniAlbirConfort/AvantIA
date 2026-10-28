[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

if ($env:COMPUTERNAME -ieq 'DESKTOP-4597Q79') {
    throw 'Instala esta tarea en el equipo local de desarrollo, no en el servidor de producción.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$watcherPath = Join-Path $PSScriptRoot 'watch-local-compose.ps1'
if (-not (Test-Path -LiteralPath $watcherPath)) {
    throw "No se encuentra el vigilante local: $watcherPath"
}

$taskName = 'AvantIA Local Auto Refresh'
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
    -Description 'Mantiene Compose Watch activo para refrescar los contenedores locales de AvantIA.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Output "Tarea '$taskName' instalada para $identity y puesta en marcha."
Write-Output "Registro: $env:LOCALAPPDATA\AvantIA\local-compose-watch.log"
