[CmdletBinding()]
param(
    [ValidateRange(60, 3600)]
    [int]$PollIntervalSeconds = 300
)

$ErrorActionPreference = 'Stop'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stateDirectory = Join-Path $env:LOCALAPPDATA 'AvantIA'
$script:LogPath = Join-Path $stateDirectory 'deploy-watch.log'
$script:DeployedRevisionPath = Join-Path $stateDirectory 'last-successful-deploy.txt'
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'Never'

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Write-DeployLog {
    param([Parameter(Mandatory)][string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $script:LogPath -Value "$timestamp $Message" -Encoding UTF8
}

function Invoke-GitText {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & git -C $script:RepoRoot @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Invoke-GitQuiet {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & git -C $script:RepoRoot @Arguments *> $null
    return $LASTEXITCODE
}

function Invoke-DeploymentCheck {
    $branch = Invoke-GitText -Arguments @('branch', '--show-current')
    if ($branch -ne 'main') {
        Write-DeployLog 'Skipped: checkout is not on main.'
        return
    }

    $remoteUrl = Invoke-GitText -Arguments @('remote', 'get-url', 'origin')
    if ($remoteUrl -ne 'https://github.com/toniAlbirConfort/AvantIA.git') {
        Write-DeployLog 'Skipped: origin does not match the configured AvantIA repository.'
        return
    }

    $workingTree = Invoke-GitText -Arguments @('status', '--porcelain')
    if ($null -eq $workingTree) {
        Write-DeployLog 'Skipped: could not inspect the working tree.'
        return
    }
    if ($workingTree.Length -gt 0) {
        Write-DeployLog 'Skipped: working tree has local changes.'
        return
    }

    if ((Invoke-GitQuiet -Arguments @('fetch', '--quiet', 'origin', 'refs/heads/main:refs/remotes/origin/main')) -ne 0) {
        Write-DeployLog 'Fetch failed; will retry at the next check.'
        return
    }

    $targetRevision = Invoke-GitText -Arguments @('rev-parse', 'refs/remotes/origin/main')
    $currentRevision = Invoke-GitText -Arguments @('rev-parse', 'HEAD')
    if (-not $targetRevision -or -not $currentRevision) {
        Write-DeployLog 'Skipped: could not read the local or remote revision.'
        return
    }

    & git -C $script:RepoRoot merge-base --is-ancestor HEAD refs/remotes/origin/main *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-DeployLog 'Skipped: local main diverges from origin/main; inspect the checkout manually.'
        return
    }

    if ($currentRevision -ne $targetRevision) {
        if ((Invoke-GitQuiet -Arguments @('pull', '--ff-only', 'origin', 'main')) -ne 0) {
            Write-DeployLog 'Fast-forward update failed; will retry at the next check.'
            return
        }
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

    Push-Location -LiteralPath $script:RepoRoot
    try {
        & docker compose -f compose.yaml -f compose.prod.yaml up --build -d *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-DeployLog "Docker Compose deployment failed (exit code $LASTEXITCODE); will retry."
            return
        }

        $runningServices = & docker compose -f compose.yaml -f compose.prod.yaml ps --services --status running 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-DeployLog "Docker Compose status check failed (exit code $LASTEXITCODE); will retry."
            return
        }

        $requiredServices = @('db', 'api', 'worker', 'gowa', 'frontend')
        $missingServices = @($requiredServices | Where-Object { $runningServices -notcontains $_ })
        if ($missingServices.Count -gt 0) {
            Write-DeployLog "Deployment incomplete; services not running: $($missingServices -join ', '). Will retry."
            return
        }

        Set-Content -LiteralPath $script:DeployedRevisionPath -Value $targetRevision -Encoding ASCII
        Write-DeployLog "Deployment completed for revision $targetRevision."
    }
    finally {
        Pop-Location
    }
}

Write-DeployLog "Watcher started; polling every $PollIntervalSeconds seconds."
while ($true) {
    try {
        Invoke-DeploymentCheck
    }
    catch {
        Write-DeployLog "Check failed with $($_.Exception.GetType().Name); will retry."
    }

    Start-Sleep -Seconds $PollIntervalSeconds
}
