$ErrorActionPreference = 'Stop'

$debugPort = 9222
$profilePath = Join-Path $env:LOCALAPPDATA 'AvantIA\AvantioChromeProfile'
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
    "--remote-debugging-port=$debugPort",
    '--remote-debugging-address=127.0.0.1',
    $profileArgument,
    'https://app.avantio.pro/'
)

Write-Host 'Se abrió un perfil Chrome dedicado de Avantio.'
Write-Host 'Inicia sesión en Avantio y abre Compromisos hasta ver el listado de reservas.'
Write-Host 'Después ejecuta scripts/capture-avantio-session.ps1 para guardar el estado de forma protegida.'
Write-Host 'Cierra este Chrome dedicado al terminar la captura para cerrar el puerto de depuración.'
