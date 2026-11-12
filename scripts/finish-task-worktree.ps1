<#
.SYNOPSIS
    Cierra una tarea: rebasa sobre origin/main, verifica, fusiona en main y limpia.
.DESCRIPTION
    Implementa el flujo descrito en AGENTS.md. Si cualquier paso falla, el script
    se detiene antes de modificar main.
.EXAMPLE
    .\scripts\finish-task-worktree.ps1 -Slug resumen-reservas
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'BySlug')]
    [ValidatePattern('^[a-z0-9]+(-[a-z0-9]+)*$')]
    [string]$Slug,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByPath')]
    [string]$Path,

    [string]$WorktreeRoot,
    [switch]$SkipChecks,
    [switch]$NoPush,
    [switch]$KeepWorktree,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$git = (Get-Command -Name 'git' -ErrorAction Stop).Source

# Sin '2>&1': en Windows PowerShell 5.1 esa redirección convierte la salida de error
# de un ejecutable nativo en ErrorRecord y aborta el script aunque git termine bien.
function Invoke-Git {
    param([string[]]$Arguments, [string]$FailureMessage)
    & $git @Arguments
    if ($LASTEXITCODE -ne 0) { throw $FailureMessage }
}

$gitDir = (& $git rev-parse --git-dir 2>$null)
if ($LASTEXITCODE -ne 0) { throw 'Ejecuta el script dentro del repositorio AvantIA.' }
$commonDir = & $git rev-parse --git-common-dir
if ((Resolve-Path $gitDir).Path -ne (Resolve-Path $commonDir).Path) {
    throw 'Estás en un worktree de tarea. Ejecuta este script desde el checkout principal (la copia que está en main).'
}

$repoRoot = (& $git rev-parse --show-toplevel) -replace '/', '\'
if (-not $WorktreeRoot) {
    $WorktreeRoot = Join-Path (Split-Path -Parent $repoRoot) 'AvantIA-worktrees'
}
if ($PSCmdlet.ParameterSetName -eq 'BySlug') {
    $worktreePath = Join-Path $WorktreeRoot $Slug
} else {
    $worktreePath = $Path
}
if (-not (Test-Path -LiteralPath $worktreePath)) { throw "No existe el worktree '$worktreePath'." }
$worktreePath = (Resolve-Path -LiteralPath $worktreePath).Path

$branch = & $git -C $worktreePath rev-parse --abbrev-ref HEAD
if ($LASTEXITCODE -ne 0) { throw "No se pudo leer la rama de '$worktreePath'." }
if ($branch -eq 'main' -or $branch -eq 'HEAD') { throw "El worktree '$worktreePath' no está en una rama de tarea." }

$dirty = & $git -C $worktreePath status --porcelain
if ($dirty) {
    $dirty | ForEach-Object { Write-Host $_ }
    throw "La rama '$branch' tiene cambios sin commitear. Haz commit o descártalos antes de cerrar la tarea."
}

$mainDirty = & $git -C $repoRoot status --porcelain
if ($mainDirty) {
    $mainDirty | ForEach-Object { Write-Host $_ }
    throw 'El checkout principal tiene cambios sin commitear. main debe estar limpio antes de integrar.'
}

Write-Host 'Actualizando referencias de origin...'
Invoke-Git -Arguments @('fetch', 'origin', '--prune') -FailureMessage 'No se pudo contactar con origin.'

$pending = & $git -C $worktreePath rev-list --count "origin/main..$branch"
if ([int]$pending -eq 0) {
    throw "La rama '$branch' no aporta commits sobre origin/main. Si su trabajo ya está integrado o quedó obsoleto, descártala con clean-merged-branches.ps1 en lugar de fusionarla."
}
Write-Host "Rama '$branch': $pending commit(s) por integrar."

Write-Host "Rebasando '$branch' sobre origin/main..."
& $git -C $worktreePath rebase origin/main
if ($LASTEXITCODE -ne 0) {
    throw "El rebase encontró conflictos. Resuélvelos en '$worktreePath' (git rebase --continue) o cancélalo (git rebase --abort) y vuelve a ejecutar este script. main no se ha tocado."
}

if ($SkipChecks) {
    Write-Host 'Verificación omitida por -SkipChecks.' -ForegroundColor Yellow
} else {
    Write-Host 'Verificando backend (pytest)...'
    $python = Get-Command -Name 'python' -ErrorAction SilentlyContinue
    if (-not $python) { throw 'No se encontró python. Instálalo o ejecuta con -SkipChecks asumiendo la responsabilidad de verificar a mano.' }
    Push-Location (Join-Path $worktreePath 'backend')
    try {
        & $python.Source -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest falló en '$branch'. No se integra en main." }
    } finally { Pop-Location }

    Write-Host 'Verificando frontend (npm run build)...'
    $npm = Get-Command -Name 'npm' -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'No se encontró npm. Instálalo o ejecuta con -SkipChecks asumiendo la responsabilidad de verificar a mano.' }
    Push-Location (Join-Path $worktreePath 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & $npm.Source ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci falló en '$branch'." }
        }
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build falló en '$branch'. No se integra en main." }
    } finally { Pop-Location }
}

Write-Host 'Actualizando main...'
Invoke-Git -Arguments @('-C', $repoRoot, 'switch', 'main') -FailureMessage 'No se pudo cambiar a main.'
Invoke-Git -Arguments @('-C', $repoRoot, 'merge', '--ff-only', 'origin/main') -FailureMessage 'main diverge de origin/main. Revísalo a mano antes de integrar.'

Write-Host "Fusionando '$branch' en main (--no-ff)..."
Invoke-Git -Arguments @('-C', $repoRoot, 'merge', '--no-ff', '-m', "Merge $branch", $branch) -FailureMessage "La fusión de '$branch' falló. main se ha quedado en el estado anterior; revisa el conflicto."

$pushed = $false
if ($NoPush) {
    Write-Host 'main integrado en local. No se publica por -NoPush.' -ForegroundColor Yellow
} else {
    $confirmed = $Yes
    if (-not $confirmed) {
        $answer = Read-Host "Publicar main en origin (despliega producción). Escribe 's' para continuar"
        $confirmed = ($answer -eq 's')
    }
    if ($confirmed) {
        Invoke-Git -Arguments @('-C', $repoRoot, 'push', 'origin', 'main') -FailureMessage 'No se pudo publicar main.'
        $pushed = $true
        Write-Host 'main publicado en origin.'
    } else {
        Write-Host 'Publicación cancelada. main queda integrado solo en local.' -ForegroundColor Yellow
    }
}

if ($KeepWorktree) {
    Write-Host "Worktree conservado en '$worktreePath' por -KeepWorktree."
} elseif (-not $pushed) {
    Write-Host "Worktree y rama '$branch' conservados: no se publicó main." -ForegroundColor Yellow
} else {
    Invoke-Git -Arguments @('worktree', 'remove', $worktreePath) -FailureMessage "No se pudo eliminar el worktree '$worktreePath'."
    Invoke-Git -Arguments @('branch', '-d', $branch) -FailureMessage "No se pudo eliminar la rama local '$branch'."
    & $git show-ref --verify --quiet "refs/remotes/origin/$branch"
    if ($LASTEXITCODE -eq 0) {
        Invoke-Git -Arguments @('push', 'origin', '--delete', $branch) -FailureMessage "No se pudo eliminar la rama '$branch' en origin."
    }
    Write-Host "Worktree y rama '$branch' eliminados."
}

Write-Host ''
Write-Host 'main:'
& $git -C $repoRoot log --oneline -3
