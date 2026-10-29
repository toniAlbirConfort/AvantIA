$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'
$localAvantiaPath = Join-Path $env:LOCALAPPDATA 'AvantIA'
$importPath = Join-Path $localAvantiaPath 'AvantioImports'
$runnerPath = Join-Path $localAvantiaPath 'HostRunner'
$pythonPath = Join-Path $runnerPath 'Scripts\python.exe'
$taskName = 'AvantIA Avantio Reservation Sync'
Set-Location -LiteralPath $repoRoot

if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw 'No se encontró .env en la raíz del repositorio.'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Instala Python 3.12 para Windows con el Python Launcher y vuelve a ejecutar este script.'
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory)][string] $Name,
        [string] $Default = ''
    )
    $contents = [System.IO.File]::ReadAllText($envPath)
    $pattern = '(?m)^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*?)\s*$'
    $match = [regex]::Match($contents, $pattern)
    if (-not $match.Success) { return $Default }
    $value = $match.Groups[1].Value.Trim()
    if ($value.Length -ge 2 -and
        (($value.StartsWith('"') -and $value.EndsWith('"')) -or
         ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        return $value.Substring(1, $value.Length - 2)
    }
    return $value -replace '\s+#.*$', ''
}

$exportUrl = Get-DotEnvValue -Name 'AVANTIO_EXPORT_URL'
if (-not $exportUrl -or -not $exportUrl.StartsWith('https://app.avantio.pro/')) {
    throw 'Configura AVANTIO_EXPORT_URL con el enlace de Compromisos de Avantio en .env.'
}
$intervalHours = 6
$intervalText = Get-DotEnvValue -Name 'AVANTIO_SYNC_INTERVAL_HOURS' -Default '6'
if (-not [int]::TryParse($intervalText, [ref]$intervalHours) -or
    $intervalHours -lt 1 -or $intervalHours -gt 168) {
    throw 'AVANTIO_SYNC_INTERVAL_HOURS debe ser un número entero entre 1 y 168.'
}

New-Item -ItemType Directory -Path $localAvantiaPath -Force | Out-Null
New-Item -ItemType Directory -Path $importPath -Force | Out-Null
New-Item -ItemType Directory -Path $runnerPath -Force | Out-Null
$composeImportPath = $importPath.Replace('\', '/')
$envContents = [System.IO.File]::ReadAllText($envPath)
$envLines = [System.Text.RegularExpressions.Regex]::Split($envContents, "`r`n|`n|`r")
$importValueWritten = $false
for ($index = 0; $index -lt $envLines.Length; $index++) {
    if ($envLines[$index] -match '^\s*AVANTIO_IMPORT_DIR\s*=') {
        $envLines[$index] = "AVANTIO_IMPORT_DIR=$composeImportPath"
        $importValueWritten = $true
        break
    }
}
if (-not $importValueWritten) {
    $envLines += "AVANTIO_IMPORT_DIR=$composeImportPath"
}
[System.IO.File]::WriteAllText(
    $envPath,
    (($envLines -join [System.Environment]::NewLine).TrimEnd() + [System.Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
)

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    & py -3.12 -m venv $runnerPath
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno Python del exportador.' }
}
& $pythonPath -m pip install --disable-pip-version-check --no-warn-script-location 'playwright==1.64.1'
if ($LASTEXITCODE -ne 0) { throw 'No se pudo instalar Playwright en el entorno del servidor.' }

$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$userAccessRule = "*${userSid}:(OI)(CI)F"
& icacls.exe $importPath /inheritance:r /grant:r `
    $userAccessRule '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron restringir los permisos de la carpeta temporal del CSV.' }

$composeOutput = & docker compose -f (Join-Path $repoRoot 'compose.yaml') `
    -f (Join-Path $repoRoot 'compose.prod.yaml') up --build -d --force-recreate api worker 2>&1
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo compilar y recrear API y worker para montar la carpeta temporal de importación.'
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument (
    '-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    (Join-Path $PSScriptRoot 'run-avantio-sync-task.ps1') + '"'
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
$null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force -ErrorAction Stop
Start-ScheduledTask -TaskName $taskName -ErrorAction Stop

Write-Host "Tarea interactiva instalada: $taskName"
Write-Host "Intervalo configurado: cada $intervalHours horas (se vuelve a leer desde .env)."
Write-Host 'Se abrió Chrome dedicado; inicia sesión en Avantio y mantenlo abierto.'
Write-Host 'El estado y los errores aparecen en Integraciones > Reservas Avantio.'
