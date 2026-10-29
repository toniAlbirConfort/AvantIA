[CmdletBinding()]
param(
    [ValidateRange(60, 3600)]
    [int]$PollIntervalSeconds = 300
)

$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:DeployRequestPath = Join-Path $script:RepoRoot '.deploy-trigger\request.json'
$stateDirectory = Join-Path $env:LOCALAPPDATA 'AvantIA'
$script:LogPath = Join-Path $stateDirectory 'deploy-watch.log'
$script:DeployedRevisionPath = Join-Path $stateDirectory 'last-successful-deploy.txt'
$script:CurrentStage = 'initialization'
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'Never'

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Write-DeployLog {
    param([Parameter(Mandatory)][string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $script:LogPath -Value "$timestamp $Message" -Encoding UTF8
}

function Invoke-NativeQuiet {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # PowerShell 5.1 turns native stderr into Error records. Keep those
        # diagnostics from aborting the check; use the process exit code below.
        $ErrorActionPreference = 'Continue'
        Get-Command -Name $Executable -ErrorAction Stop | Out-Null
        & $Executable @Arguments *> $null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return $exitCode
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        Get-Command -Name $Executable -ErrorAction Stop | Out-Null
        $output = @(& $Executable @Arguments 2>$null)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
    }
}

function Invoke-GitText {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $gitArguments = @('-C', $script:RepoRoot) + $Arguments
    $result = Invoke-NativeCapture -Executable 'git' -Arguments $gitArguments
    if ($result.ExitCode -ne 0) {
        return $null
    }

    return (($result.Output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Invoke-GitQuiet {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $gitArguments = @('-C', $script:RepoRoot) + $Arguments
    return (Invoke-NativeQuiet -Executable 'git' -Arguments $gitArguments)
}

function Invoke-DockerQuiet {
    param([Parameter(Mandatory)][string[]]$Arguments)

    return (Invoke-NativeQuiet -Executable 'docker' -Arguments $Arguments)
}

function Invoke-DockerCapture {
    param([Parameter(Mandatory)][string[]]$Arguments)

    return (Invoke-NativeCapture -Executable 'docker' -Arguments $Arguments)
}

function Invoke-DeploymentCheck {
    $script:CurrentStage = 'checking the branch'
    $branch = Invoke-GitText -Arguments @('branch', '--show-current')
    if ($branch -ne 'main') {
        Write-DeployLog 'Skipped: checkout is not on main.'
        return
    }

    $script:CurrentStage = 'checking the origin URL'
    $remoteUrl = Invoke-GitText -Arguments @('remote', 'get-url', 'origin')
    if ($remoteUrl -ne 'https://github.com/toniAlbirConfort/AvantIA.git') {
        Write-DeployLog 'Skipped: origin does not match the configured AvantIA repository.'
        return
    }

    $script:CurrentStage = 'checking the working tree'
    $workingTree = Invoke-GitText -Arguments @('status', '--porcelain')
    if ($null -eq $workingTree) {
        Write-DeployLog 'Skipped: could not inspect the working tree.'
        return
    }
    if ($workingTree.Length -gt 0) {
        Write-DeployLog 'Skipped: working tree has local changes.'
        return
    }

    $script:CurrentStage = 'fetching origin/main'
    $fetchExitCode = Invoke-GitQuiet -Arguments @('fetch', '--quiet', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    if ($fetchExitCode -ne 0) {
        Write-DeployLog "Fetch failed (exit code $fetchExitCode); will retry at the next check."
        return
    }

    $script:CurrentStage = 'reading the fetched revisions'
    $targetRevision = Invoke-GitText -Arguments @('rev-parse', 'refs/remotes/origin/main')
    $currentRevision = Invoke-GitText -Arguments @('rev-parse', 'HEAD')
    if (-not $targetRevision -or -not $currentRevision) {
        Write-DeployLog 'Skipped: could not read the local or remote revision.'
        return
    }

    $script:CurrentStage = 'checking fast-forward safety'
    $ancestryExitCode = Invoke-GitQuiet -Arguments @('merge-base', '--is-ancestor', 'HEAD', 'refs/remotes/origin/main')
    if ($ancestryExitCode -ne 0) {
        Write-DeployLog 'Skipped: local main diverges from origin/main; inspect the checkout manually.'
        return
    }

    if ($currentRevision -ne $targetRevision) {
        $script:CurrentStage = 'updating the checkout from origin/main'
        $pullExitCode = Invoke-GitQuiet -Arguments @('pull', '--ff-only', 'origin', 'main')
        if ($pullExitCode -ne 0) {
            Write-DeployLog "Fast-forward update failed (exit code $pullExitCode); will retry at the next check."
            return
        }
        $script:CurrentStage = 'verifying the updated checkout'
        $currentRevision = Invoke-GitText -Arguments @('rev-parse', 'HEAD')
        if ($currentRevision -ne $targetRevision) {
            Write-DeployLog 'Skipped: checkout did not reach the fetched origin/main revision.'
            return
        }
    }

    $lastSuccessfulRevision = $null
    if (Test-Path -LiteralPath $script:DeployedRevisionPath) {
        $lastSuccessfulRevision = (Get-Content -LiteralPath $script:DeployedRevisionPath -Raw).Trim()
    }
    if ($lastSuccessfulRevision -eq $targetRevision) {
        return
    }

    $script:CurrentStage = 'building and starting production services'
    Push-Location -LiteralPath $script:RepoRoot
    try {
        $deployExitCode = Invoke-DockerQuiet -Arguments @('compose', '-f', 'compose.yaml', '-f', 'compose.prod.yaml', 'up', '--build', '-d')
        if ($deployExitCode -ne 0) {
            Write-DeployLog "Docker Compose deployment failed (exit code $deployExitCode); will retry."
            return
        }

        $script:CurrentStage = 'checking the running services'
        $statusResult = Invoke-DockerCapture -Arguments @('compose', '-f', 'compose.yaml', '-f', 'compose.prod.yaml', 'ps', '--services', '--status', 'running')
        if ($statusResult.ExitCode -ne 0) {
            Write-DeployLog "Docker Compose status check failed (exit code $($statusResult.ExitCode)); will retry."
            return
        }

        $runningServices = $statusResult.Output
        $requiredServices = @('db', 'api', 'worker', 'gowa', 'frontend')
        $missingServices = @($requiredServices | Where-Object { $runningServices -notcontains $_ })
        if ($missingServices.Count -gt 0) {
            Write-DeployLog "Deployment incomplete; services not running: $($missingServices -join ', '). Will retry."
            return
        }

        $script:CurrentStage = 'recording the successful revision'
        Set-Content -LiteralPath $script:DeployedRevisionPath -Value $targetRevision -Encoding ASCII
        Write-DeployLog "Deployment completed for revision $targetRevision."
    }
    finally {
        Pop-Location
    }
}

function Wait-ForDeploymentRequest {
    param([Parameter(Mandatory)][int]$TimeoutSeconds)

    for ($elapsed = 0; $elapsed -lt $TimeoutSeconds; $elapsed++) {
        if (Test-Path -LiteralPath $script:DeployRequestPath) {
            $claimedPath = "$($script:DeployRequestPath).processing-$([guid]::NewGuid().ToString('N'))"
            try {
                Move-Item -LiteralPath $script:DeployRequestPath -Destination $claimedPath -ErrorAction Stop
                Remove-Item -LiteralPath $claimedPath -Force -ErrorAction Stop
                Write-DeployLog 'GitHub merge webhook received; checking origin/main immediately.'
                return $true
            }
            catch {
                if (Test-Path -LiteralPath $script:DeployRequestPath) {
                    Write-DeployLog "Could not consume the deployment request ($($_.Exception.GetType().Name)); will retry at the next check."
                    return $false
                }
            }
        }

        Start-Sleep -Seconds 1
    }

    return $false
}

Write-DeployLog "Watcher started; checking every $PollIntervalSeconds seconds and waiting for webhook requests."
while ($true) {
    try {
        Invoke-DeploymentCheck
    }
    catch {
        Write-DeployLog "Check failed during $script:CurrentStage ($($_.Exception.GetType().Name)); will retry."
    }

    [void](Wait-ForDeploymentRequest -TimeoutSeconds $PollIntervalSeconds)
}
