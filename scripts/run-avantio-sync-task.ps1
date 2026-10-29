$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'
$localAvantiaPath = Join-Path $env:LOCALAPPDATA 'AvantIA'
$importPath = Join-Path $localAvantiaPath 'AvantioImports'
$logPath = Join-Path $localAvantiaPath 'avantio-sync.log'
$pythonPath = Join-Path $localAvantiaPath 'HostRunner\Scripts\python.exe'
$exportModule = 'app.avantio_chrome_export'
$syncCli = 'app.reservation_sync_cli'
$composeFiles = @('-f', 'compose.yaml', '-f', 'compose.prod.yaml')
$lastAttemptAt = $null
$lastRunSucceeded = $false
$lastReportedInterval = 0
$lastIntervalReportAttempt = [DateTimeOffset]::MinValue

Set-Location -LiteralPath $repoRoot
$env:AVANTIO_IMPORT_DIR = $importPath
$env:PYTHONPATH = Join-Path $repoRoot 'backend'

function Write-SyncLog {
    param([Parameter(Mandatory)][string] $Message)
    $timestamp = [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz')
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message" -Encoding UTF8
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

function Invoke-ComposeWorker {
    param([Parameter(Mandatory)][string[]] $Arguments)
    $output = & docker compose @composeFiles exec -T worker python -m $syncCli @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $message = "El worker no pudo registrar la sincronización (código $LASTEXITCODE)."
        if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'import') {
            $lastLine = @($output | ForEach-Object { [string]$_ } |
                Where-Object { $_ -and $_ -notmatch 'Traceback|postgresql://|password|File ".*"' } |
                Select-Object -Last 1)
            if ($lastLine.Count -gt 0 -and $lastLine[0].Length -le 500) {
                $message = "No se pudo validar o importar el CSV: $($lastLine[0])"
            }
        }
        throw $message
    }
    return @($output)
}

function Clear-StaleImportFiles {
    $cutoff = [DateTime]::Now.AddHours(-2)
    $importRoot = [System.IO.Path]::GetFullPath($importPath).TrimEnd('\', '/')
    $rootPrefix = $importRoot + [System.IO.Path]::DirectorySeparatorChar
    Get-ChildItem -LiteralPath $importPath -File -Filter 'avantio-*.csv' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $importPath -Directory -Filter 'avantio-download-*' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object {
            $target = [System.IO.Path]::GetFullPath($_.FullName)
            if ($target.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                (Split-Path -Parent $target) -eq $importRoot -and
                $_.Name -like 'avantio-download-*') {
                Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
}

function Invoke-AvantioSync {
    $exportUrl = Get-DotEnvValue -Name 'AVANTIO_EXPORT_URL'
    if (-not $exportUrl -or -not $exportUrl.StartsWith('https://app.avantio.pro/')) {
        throw 'AVANTIO_EXPORT_URL no está configurada correctamente.'
    }
    $env:AVANTIO_EXPORT_URL = $exportUrl

    $csvName = 'avantio-' + [guid]::NewGuid().ToString('N') + '.csv'
    $hostCsvPath = Join-Path $importPath $csvName
    $containerCsvPath = '/run/avantio-import/' + $csvName
    try {
        Invoke-ComposeWorker -Arguments @('start') | Out-Null
        & (Join-Path $PSScriptRoot 'start-avantio-session.ps1') | Out-Null
        $exportOutput = & $pythonPath -m $exportModule --output $hostCsvPath 2>&1
        $exportExitCode = $LASTEXITCODE
        if ($exportExitCode -ne 0) {
            $safeMessage = @($exportOutput | ForEach-Object { [string]$_ }) -join ' '
            if (-not $safeMessage -or $safeMessage -match 'https?://|Traceback|password|AVANTIO_USERNAME') {
                $safeMessage = 'No se pudo exportar el CSV desde Chrome dedicado.'
            }
            $safeMessage = $safeMessage -replace '[\r\n]+', ' '
            if ($safeMessage.Length -gt 500) { $safeMessage = $safeMessage.Substring(0, 500) }
            Invoke-ComposeWorker -Arguments @('fail', '--message', $safeMessage) | Out-Null
            Write-SyncLog -Message "Sincronización fallida: $safeMessage"
            return $false
        }

        $importOutput = Invoke-ComposeWorker -Arguments @('import', $containerCsvPath)
        $summary = @($importOutput | ForEach-Object { [string]$_ }) -join ' '
        Write-SyncLog -Message $(if ($summary) { $summary } else { 'Sincronización completada.' })
        return $true
    } catch {
        $safeMessage = [string]$_.Exception.Message
        if ($safeMessage -match 'https?://|AVANTIO_PASSWORD|AVANTIO_USERNAME') {
            $safeMessage = 'Error durante la exportación automática de Avantio.'
        }
        try {
            Invoke-ComposeWorker -Arguments @('fail', '--message', $safeMessage) | Out-Null
        } catch {
            Write-SyncLog -Message 'No se pudo guardar el error de sincronización en la aplicación.'
        }
        Write-SyncLog -Message "Sincronización fallida: $safeMessage"
        return $false
    } finally {
        Remove-Item -LiteralPath $hostCsvPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-SyncLog -Message 'No se encuentra el entorno Python de Playwright; vuelve a ejecutar install-avantio-sync-task.ps1.'
    exit 1
}

while ($true) {
    $intervalHours = 6
    $intervalText = Get-DotEnvValue -Name 'AVANTIO_SYNC_INTERVAL_HOURS' -Default '6'
    if (-not [int]::TryParse($intervalText, [ref]$intervalHours) -or
        $intervalHours -lt 1 -or $intervalHours -gt 168) {
        Write-SyncLog -Message 'AVANTIO_SYNC_INTERVAL_HOURS debe ser un entero entre 1 y 168.'
        Start-Sleep -Seconds 60
        continue
    }

    $now = [DateTimeOffset]::Now
    if ($lastReportedInterval -ne $intervalHours -and
        ($now - $lastIntervalReportAttempt).TotalMinutes -ge 5) {
        $lastIntervalReportAttempt = $now
        try {
            Invoke-ComposeWorker -Arguments @('set-interval', [string]$intervalHours) | Out-Null
            $lastReportedInterval = $intervalHours
        } catch {
            if ($lastReportedInterval -eq 0) {
                Write-SyncLog -Message 'No se pudo publicar el intervalo configurado en Integraciones.'
            }
        }
    }

    $waitInterval = if ($lastRunSucceeded) {
        [TimeSpan]::FromHours($intervalHours)
    } else {
        [TimeSpan]::FromMinutes(15)
    }
    if ($null -eq $lastAttemptAt -or ($now - $lastAttemptAt) -ge $waitInterval) {
        $lastAttemptAt = $now
        Clear-StaleImportFiles
        $lastRunSucceeded = Invoke-AvantioSync
    }
    Start-Sleep -Seconds 30
}
