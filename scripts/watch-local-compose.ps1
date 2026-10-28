[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($env:COMPUTERNAME -ieq 'DESKTOP-4597Q79') {
    throw 'El auto-refresh local no se puede ejecutar en el servidor de producción.'
}

if (-not $env:LOCALAPPDATA) {
    throw 'No está disponible LOCALAPPDATA para guardar el registro del vigilante.'
}

$stateDirectory = Join-Path $env:LOCALAPPDATA 'AvantIA'
$script:LogPath = Join-Path $stateDirectory 'local-compose-watch.log'
$minimumComposeVersion = [version]'2.22.0'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Write-RefreshLog {
    param([Parameter(Mandatory)][string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $script:LogPath -Value "$timestamp $Message" -Encoding UTF8
}

Write-RefreshLog 'Local Compose Watch monitor started.'
while ($true) {
    try {
        $versionOutput = & docker compose version 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-RefreshLog 'Docker Compose is unavailable; retrying in 60 seconds.'
            Start-Sleep -Seconds 60
            continue
        }

        $versionText = ($versionOutput | ForEach-Object { [string]$_ }) -join ' '
        $versionMatch = [regex]::Match($versionText, '\bv?(\d+)\.(\d+)\.(\d+)')
        if (-not $versionMatch.Success) {
            Write-RefreshLog 'Could not read the Docker Compose version; retrying in 60 seconds.'
            Start-Sleep -Seconds 60
            continue
        }

        $composeVersion = [version]::new(
            [int]$versionMatch.Groups[1].Value,
            [int]$versionMatch.Groups[2].Value,
            [int]$versionMatch.Groups[3].Value
        )
        if ($composeVersion -lt $minimumComposeVersion) {
            Write-RefreshLog "Compose Watch requires version $minimumComposeVersion or later; retrying in 60 seconds."
            Start-Sleep -Seconds 60
            continue
        }

        Push-Location -LiteralPath $script:RepoRoot
        try {
            & docker compose -f compose.yaml -f compose.dev.yaml watch api worker frontend *> $null
            $watchExitCode = $LASTEXITCODE
            Write-RefreshLog "Compose Watch stopped with exit code $watchExitCode; retrying in 30 seconds."
        }
        finally {
            Pop-Location
        }
    }
    catch {
        Write-RefreshLog "Compose Watch failed with $($_.Exception.GetType().Name); retrying in 60 seconds."
    }

    Start-Sleep -Seconds 30
}
