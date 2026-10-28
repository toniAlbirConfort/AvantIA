[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SshTarget,

    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 11435
)

$ErrorActionPreference = 'Stop'
$ssh = Get-Command -Name 'ssh.exe' -ErrorAction Stop
$forward = "127.0.0.1:${LocalPort}:127.0.0.1:11434"

Write-Host "Túnel Ollama local en 127.0.0.1:$LocalPort hacia $SshTarget (Ctrl+C para cerrar)."
& $ssh.Source -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L $forward $SshTarget
exit $LASTEXITCODE
