<#
.SYNOPSIS
    Informa de qué ramas ya están integradas en main y, opcionalmente, las elimina.
.DESCRIPTION
    Compara cada rama con origin/main por contenido (git cherry), no por historial,
    así que detecta también las ramas integradas mediante squash o commits directos,
    que 'git branch --merged' no reconoce.
    Sin -Delete solo informa. Con -Delete crea antes un bundle con todas las
    referencias del repositorio en ..\AvantIA-backups, para poder recuperar
    cualquier rama borrada.
.EXAMPLE
    .\scripts\clean-merged-branches.ps1
.EXAMPLE
    .\scripts\clean-merged-branches.ps1 -Delete
#>
[CmdletBinding()]
param(
    [switch]$Delete,
    [switch]$Yes,
    [string]$BackupRoot
)

$ErrorActionPreference = 'Stop'
$git = (Get-Command -Name 'git' -ErrorAction Stop).Source

$gitDir = (& $git rev-parse --git-dir 2>$null)
if ($LASTEXITCODE -ne 0) { throw 'Ejecuta el script dentro del repositorio AvantIA.' }
$commonDir = & $git rev-parse --git-common-dir
if ((Resolve-Path $gitDir).Path -ne (Resolve-Path $commonDir).Path) {
    throw 'Estás en un worktree de tarea. Ejecuta este script desde el checkout principal (la copia que está en main).'
}

Write-Host 'Actualizando referencias de origin...'
& $git fetch origin --prune | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No se pudo contactar con origin.' }

# Ramas ocupadas por un worktree abierto: son tareas en curso, no se tocan.
$busy = @{}
foreach ($line in (& $git worktree list --porcelain)) {
    if ($line -like 'branch refs/heads/*') {
        $busy[$line.Substring('branch refs/heads/'.Length)] = $true
    }
}

$branches = @()
$names = @()
foreach ($ref in (& $git for-each-ref --format='%(refname)' refs/heads refs/remotes/origin)) {
    if ($ref -eq 'refs/remotes/origin/HEAD') { continue }
    if ($ref -like 'refs/heads/*') { $name = $ref.Substring('refs/heads/'.Length) }
    else { $name = $ref.Substring('refs/remotes/origin/'.Length) }
    if ($name -eq 'main') { continue }
    if ($names -notcontains $name) { $names += $name }
}

foreach ($name in $names) {
    $hasLocal = $false
    & $git show-ref --verify --quiet "refs/heads/$name"
    if ($LASTEXITCODE -eq 0) { $hasLocal = $true }
    $hasRemote = $false
    & $git show-ref --verify --quiet "refs/remotes/origin/$name"
    if ($LASTEXITCODE -eq 0) { $hasRemote = $true }

    if ($hasRemote) { $tip = "origin/$name" } else { $tip = $name }
    $pending = @(& $git cherry origin/main $tip | Where-Object { $_ -like '+*' }).Count
    $lastDate = & $git log -1 --format=%ad --date=short $tip

    $branches += [pscustomobject]@{
        Nombre    = $name
        Estado    = $(if ($busy.ContainsKey($name)) { 'En curso' } elseif ($pending -eq 0) { 'Integrada' } else { "Pendiente ($pending)" })
        Pendiente = $pending
        Local     = $hasLocal
        Origin    = $hasRemote
        Ultimo    = $lastDate
        EnCurso   = $busy.ContainsKey($name)
    }
}

$branches |
    Sort-Object -Property @{Expression = 'Pendiente'; Descending = $true}, 'Nombre' |
    Format-Table -AutoSize -Property Nombre, Estado, Local, Origin, Ultimo

$integrated = @($branches | Where-Object { -not $_.EnCurso -and $_.Pendiente -eq 0 })
$pendientes = @($branches | Where-Object { -not $_.EnCurso -and $_.Pendiente -gt 0 })
$enCurso = @($branches | Where-Object { $_.EnCurso })

Write-Host ''
Write-Host ("Integradas en main: {0}   Con trabajo pendiente: {1}   En curso (worktree abierto): {2}" -f $integrated.Count, $pendientes.Count, $enCurso.Count)

if ($pendientes.Count -gt 0) {
    Write-Host ''
    Write-Host 'Revisa las ramas pendientes antes de fusionarlas: si están desfasadas respecto a main,'
    Write-Host 'fusionarlas puede revertir trabajo posterior. Compruébalo con:'
    Write-Host '  git diff origin/main origin/<rama>'
}

if (-not $Delete) {
    if ($integrated.Count -gt 0) {
        Write-Host ''
        Write-Host 'Para eliminar las ramas integradas (local y origin), vuelve a ejecutar con -Delete.'
    }
    return
}

if ($integrated.Count -eq 0) {
    Write-Host ''
    Write-Host 'No hay ramas integradas que eliminar.'
    return
}

Write-Host ''
Write-Host ("Se eliminarán {0} rama(s) en local y en origin. La eliminación en origin es irreversible." -f $integrated.Count) -ForegroundColor Yellow
if (-not $Yes) {
    $answer = Read-Host "Escribe 's' para continuar"
    if ($answer -ne 's') {
        Write-Host 'Cancelado. No se ha eliminado nada.'
        return
    }
}

$repoRoot = (& $git rev-parse --show-toplevel) -replace '/', '\'
if (-not $BackupRoot) {
    $BackupRoot = Join-Path (Split-Path -Parent $repoRoot) 'AvantIA-backups'
}
if (-not (Test-Path -LiteralPath $BackupRoot)) {
    New-Item -ItemType Directory -Path $BackupRoot | Out-Null
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bundlePath = Join-Path $BackupRoot "ramas-$stamp.bundle"
$manifestPath = Join-Path $BackupRoot "ramas-$stamp.txt"

Write-Host ''
Write-Host 'Creando copia de seguridad de todas las referencias...'
& $git for-each-ref --format='%(objectname) %(refname)' | Set-Content -LiteralPath $manifestPath -Encoding utf8
& $git bundle create $bundlePath --all
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el bundle de respaldo. No se ha eliminado ninguna rama.' }
Write-Host "Respaldo: $bundlePath"
Write-Host "Inventario: $manifestPath"
Write-Host ''

foreach ($branch in $integrated) {
    if ($branch.Local) {
        & $git branch -D $branch.Nombre | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host ("  ! no se pudo eliminar la rama local {0}" -f $branch.Nombre) -ForegroundColor Red }
    }
    if ($branch.Origin) {
        & $git push origin --delete $branch.Nombre | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host ("  ! no se pudo eliminar {0} en origin" -f $branch.Nombre) -ForegroundColor Red }
    }
    Write-Host ("  - {0}" -f $branch.Nombre)
}

& $git fetch origin --prune | Out-Null
Write-Host ''
Write-Host 'Limpieza terminada. main conserva todo el trabajo integrado.'
Write-Host "Para recuperar una rama del respaldo: git fetch `"$bundlePath`" <rama>:<rama>"
