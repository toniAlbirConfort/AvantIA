<#
.SYNOPSIS
    Abre una tarea nueva: crea la rama desde origin/main y su worktree dedicado.
.DESCRIPTION
    Implementa el flujo descrito en AGENTS.md. El checkout principal se queda en
    main; la tarea se desarrolla en ..\AvantIA-worktrees\<slug>.
.EXAMPLE
    .\scripts\new-task-worktree.ps1 -Type feat -Slug resumen-reservas
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('feat', 'fix', 'refactor', 'docs', 'chore')]
    [string]$Type,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]+(-[a-z0-9]+)*$')]
    [string]$Slug,

    [string]$WorktreeRoot
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

$branch = "codex/$Type/$Slug"
$worktreePath = Join-Path $WorktreeRoot $Slug

Write-Host "Actualizando referencias de origin..."
Invoke-Git -Arguments @('fetch', 'origin', '--prune') -FailureMessage 'No se pudo contactar con origin.'

& $git show-ref --verify --quiet "refs/heads/$branch"
if ($LASTEXITCODE -eq 0) { throw "La rama local '$branch' ya existe. Elige otro slug o cierra la tarea anterior." }
& $git show-ref --verify --quiet "refs/remotes/origin/$branch"
if ($LASTEXITCODE -eq 0) { throw "La rama '$branch' ya existe en origin. Elige otro slug o cierra la tarea anterior." }
if (Test-Path -LiteralPath $worktreePath) { throw "La carpeta '$worktreePath' ya existe. Elimínala o elige otro slug." }

if (-not (Test-Path -LiteralPath $WorktreeRoot)) {
    New-Item -ItemType Directory -Path $WorktreeRoot | Out-Null
}

# --no-track: la rama de tarea no debe seguir a origin/main, o git status informaría del estado de main.
Invoke-Git -Arguments @('worktree', 'add', '--no-track', '-b', $branch, $worktreePath, 'origin/main') -FailureMessage 'No se pudo crear el worktree.'

$baseCommit = & $git rev-parse --short origin/main
Write-Host ''
Write-Host "Rama '$branch' creada desde origin/main ($baseCommit)."
Write-Host "Worktree: $worktreePath"
Write-Host ''
Write-Host 'Trabaja ahí y haz commits descriptivos. Al terminar, desde esta carpeta:'
Write-Host "  .\scripts\finish-task-worktree.ps1 -Slug $Slug"
