$ErrorActionPreference = 'Stop'

$debugBase = 'http://127.0.0.1:9222'
$stateDirectory = Join-Path $env:LOCALAPPDATA 'AvantIA\AvantioAuth'
$stateFile = Join-Path $stateDirectory 'storage-state.json'
$repoEnvPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'

function Invoke-CdpCommand {
    param(
        [Parameter(Mandatory)]
        [System.Net.WebSockets.ClientWebSocket] $Socket,
        [Parameter(Mandatory)]
        [int] $Id,
        [Parameter(Mandatory)]
        [string] $Method,
        [hashtable] $Parameters = @{}
    )

    $payload = ConvertTo-Json -InputObject @{
        id = $Id
        method = $Method
        params = $Parameters
    } -Depth 20 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $segment = [System.ArraySegment[byte]]::new($bytes)
    $Socket.SendAsync(
        $segment,
        [System.Net.WebSockets.WebSocketMessageType]::Text,
        $true,
        [System.Threading.CancellationToken]::None
    ).GetAwaiter().GetResult()

    while ($true) {
        $messageStream = [System.IO.MemoryStream]::new()
        do {
            $buffer = [byte[]]::new(16384)
            $receiveSegment = [System.ArraySegment[byte]]::new($buffer)
            $received = $Socket.ReceiveAsync(
                $receiveSegment,
                [System.Threading.CancellationToken]::None
            ).GetAwaiter().GetResult()
            $messageStream.Write($buffer, 0, $received.Count)
        } while (-not $received.EndOfMessage)

        $message = [System.Text.Encoding]::UTF8.GetString($messageStream.ToArray()) |
            ConvertFrom-Json
        if ($message.id -ne $Id) {
            continue
        }
        if ($message.error) {
            throw "Chrome DevTools rechazó $Method ($($message.error.code))."
        }
        return $message
    }
}

function Connect-Cdp {
    param([Parameter(Mandatory)][string] $Url)
    $socket = [System.Net.WebSockets.ClientWebSocket]::new()
    $socket.ConnectAsync(
        [Uri]::new($Url),
        [System.Threading.CancellationToken]::None
    ).GetAwaiter().GetResult()
    return $socket
}

try {
    $browserInfo = Invoke-RestMethod -Uri "$debugBase/json/version" -TimeoutSec 5
} catch {
    throw 'No se encontró Chrome con depuración local en el puerto 9222. Ejecuta primero scripts/start-avantio-session.ps1.'
}

$targets = Invoke-RestMethod -Uri "$debugBase/json/list" -TimeoutSec 5
$avantioPage = @($targets | Where-Object {
    $_.type -eq 'page' -and $_.url -and ([Uri]$_.url).Host -eq 'app.avantio.pro'
} | Select-Object -First 1)
if (-not $avantioPage) {
    throw 'Abre Avantio en el perfil dedicado antes de guardar la sesión.'
}

$browserSocket = $null
$pageSocket = $null
try {
    $browserSocket = Connect-Cdp -Url $browserInfo.webSocketDebuggerUrl
    $cookieResponse = Invoke-CdpCommand -Socket $browserSocket -Id 1 -Method 'Storage.getCookies'
    $avantioCookies = @($cookieResponse.result.cookies | Where-Object {
        $domain = ([string]$_.domain).TrimStart('.')
        $domain -eq 'avantio.pro' -or $domain.EndsWith('.avantio.pro')
    })
    if (-not $avantioCookies) {
        throw 'Chrome no tiene cookies de Avantio en el perfil dedicado. Inicia sesión y vuelve a intentarlo.'
    }

    $pageSocket = Connect-Cdp -Url $avantioPage.webSocketDebuggerUrl
    $loginCheck = Invoke-CdpCommand -Socket $pageSocket -Id 2 -Method 'Runtime.evaluate' -Parameters @{
        expression = "Boolean([...document.querySelectorAll('input[type=password]')].some(el => el.offsetParent !== null))"
        returnByValue = $true
    }
    if ($loginCheck.result.result.value) {
        throw 'La página de Avantio todavía muestra el formulario de acceso. Termina el login antes de capturar el estado.'
    }

    $localStorageResponse = Invoke-CdpCommand -Socket $pageSocket -Id 3 -Method 'Runtime.evaluate' -Parameters @{
        expression = 'JSON.stringify(Object.fromEntries(Object.entries(localStorage)))'
        returnByValue = $true
    }
    $localStorageItems = @()
    $localStorageJson = $localStorageResponse.result.result.value
    if ($localStorageJson) {
        $localStorageObject = ConvertFrom-Json -InputObject $localStorageJson
        foreach ($property in $localStorageObject.PSObject.Properties) {
            $localStorageItems += @{
                name = $property.Name
                value = [string]$property.Value
            }
        }
    }

    $storageCookies = @()
    foreach ($cookie in $avantioCookies) {
        $item = [ordered]@{
            name = [string]$cookie.name
            value = [string]$cookie.value
            domain = [string]$cookie.domain
            path = [string]$cookie.path
            expires = [double]$cookie.expires
            httpOnly = [bool]$cookie.httpOnly
            secure = [bool]$cookie.secure
        }
        if (@('Strict', 'Lax', 'None') -contains [string]$cookie.sameSite) {
            $item.sameSite = [string]$cookie.sameSite
        }
        $storageCookies += $item
    }

    $state = [ordered]@{
        cookies = $storageCookies
        origins = @(
            @{
                origin = 'https://app.avantio.pro'
                localStorage = $localStorageItems
            }
        )
    }
    $json = ConvertTo-Json -InputObject $state -Depth 20 -Compress

    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    $identity = "$($env:USERDOMAIN)\$($env:USERNAME)"
    & icacls.exe $stateDirectory /inheritance:r /grant:r `
        "$($identity):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'No se pudieron restringir los permisos del directorio del estado de sesión.'
    }
    [System.IO.File]::WriteAllText(
        $stateFile,
        $json,
        [System.Text.UTF8Encoding]::new($false)
    )
    & icacls.exe $stateFile /inheritance:r /grant:r `
        "$($identity):F" '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
        throw 'No se pudieron restringir los permisos del archivo de estado.'
    }

    if (-not (Test-Path -LiteralPath $repoEnvPath -PathType Leaf)) {
        throw 'No se encontró el .env del servidor junto al repositorio.'
    }
    $composeStateDirectory = $stateDirectory.Replace('\', '/')
    $envContents = [System.IO.File]::ReadAllText($repoEnvPath)
    $envLines = [System.Text.RegularExpressions.Regex]::Split($envContents, "`r`n|`n|`r")
    $envVariableFound = $false
    for ($index = 0; $index -lt $envLines.Length; $index++) {
        if ($envLines[$index] -match '^AVANTIO_STORAGE_STATE_DIR=') {
            $envLines[$index] = "AVANTIO_STORAGE_STATE_DIR=$composeStateDirectory"
            $envVariableFound = $true
            break
        }
    }
    if (-not $envVariableFound) {
        $envLines += "AVANTIO_STORAGE_STATE_DIR=$composeStateDirectory"
    }
    [System.IO.File]::WriteAllText(
        $repoEnvPath,
        (($envLines -join [System.Environment]::NewLine).TrimEnd() + [System.Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "Estado de Avantio guardado con permisos restringidos en: $stateFile"
    Write-Host "Cookies de Avantio: $($storageCookies.Count); entradas de almacenamiento local: $($localStorageItems.Count)."
    Write-Host 'Se actualizó AVANTIO_STORAGE_STATE_DIR en el .env sin mostrar sus otros valores.'
    Write-Host 'No compartas el archivo de estado ni lo subas al repositorio.'
} finally {
    if ($pageSocket) { $pageSocket.Dispose() }
    if ($browserSocket) { $browserSocket.Dispose() }
}
