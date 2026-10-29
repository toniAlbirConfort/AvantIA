$ErrorActionPreference = 'Stop'

$debugPort = 9222
$profilePath = Join-Path $env:LOCALAPPDATA 'AvantIA\AvantioChromeProfile'

if (Test-NetConnection -ComputerName 127.0.0.1 -Port $debugPort -InformationLevel Quiet) {
    $dedicatedChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($profilePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Select-Object -First 1
    if (-not $dedicatedChrome) {
        throw "El puerto local $debugPort está ocupado por un Chrome distinto del perfil dedicado de Avantio."
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$debugPort/json/version" -TimeoutSec 5 | Out-Null
    } catch {
        throw 'El Chrome dedicado no responde en su puerto local de depuración.'
    }
    Write-Host 'El Chrome dedicado de Avantio ya está abierto.'
    Write-Host 'Inicia sesión en Avantio si aparece el formulario y mantén abierto este Chrome.'
    return
}

$chromeCandidates = @()
if ($env:ProgramFiles) {
    $chromeCandidates += Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
}
if (${env:ProgramFiles(x86)}) {
    $chromeCandidates += Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'
}
if ($env:LOCALAPPDATA) {
    $chromeCandidates += Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'
}

$chromePath = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chromePath) {
    throw 'No se encontró Google Chrome en las ubicaciones habituales.'
}

if (Test-NetConnection -ComputerName 127.0.0.1 -Port $debugPort -InformationLevel Quiet) {
    throw "El puerto local $debugPort ya está ocupado. Cierra el Chrome dedicado antes de volver a abrirlo."
}

New-Item -ItemType Directory -Path $profilePath -Force | Out-Null
$profileArgument = '--user-data-dir="' + $profilePath + '"'
Start-Process -FilePath $chromePath -ArgumentList @(
    '--no-first-run',
    '--no-default-browser-check',
    "--remote-debugging-port=$debugPort",
    '--remote-debugging-address=127.0.0.1',
    $profileArgument,
    'https://app.avantio.pro/'
)

$cdpReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$debugPort/json/version" -TimeoutSec 2 | Out-Null
        $cdpReady = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $cdpReady) {
    throw 'Chrome no abrió el puerto local de depuración dentro del tiempo límite.'
}

Write-Host 'Se abrió un perfil Chrome dedicado de Avantio.'
Write-Host 'Inicia sesión en Avantio en este Chrome y deja abierta la sesión.'
Write-Host 'La tarea AvantIA Avantio Reservation Sync usa este perfil para exportar reservas.'
