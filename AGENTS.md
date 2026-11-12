# Instrucciones de trabajo para AvantIA

## Servidor de producción e integración

- El servidor local de producción es `DESKTOP-4597Q79`, accesible por SSH como `informatica2@192.168.0.158`.
- La carpeta contenedora en el servidor es `C:\PROYECTOS ANTIGRAVITY`; la raíz del clon de este repositorio es `C:\PROYECTOS ANTIGRAVITY\AvantIA`.
- El remoto canónico es `https://github.com/toniAlbirConfort/AvantIA.git` y la rama de despliegue es `main` (`origin/main`).
- El despliegue se automatiza con la tarea programada `AvantIA Production Auto Deploy`. Un webhook firmado de GitHub despierta la tarea cuando se fusiona un PR en `main`; el sondeo de `origin/main` cada cinco minutos queda como respaldo. La tarea requiere que la cuenta que ejecuta Docker Desktop esté conectada.
- La tarea debe ejecutarse en la sesión interactiva de esa cuenta, porque Docker Desktop y su almacén de credenciales están vinculados a ella. No se debe configurar como una tarea que se ejecute aunque el usuario no haya iniciado sesión.

## Flujo de trabajo con ramas y worktrees

`main` es la única rama permanente: siempre debe estar limpia, integrada y desplegable. Nunca se trabaja directamente sobre `main` ni se publican en ella commits de trabajo sin verificar.

### Reglas

1. Cada tarea se implementa en una rama nueva y exclusiva, creada **siempre desde `origin/main`** actualizado (`git fetch origin` antes de crearla). Nunca partas de una rama local desactualizada ni de otra rama de tarea.
2. Cada rama de tarea vive en un **worktree propio**, fuera del repositorio principal. El checkout principal se queda siempre en `main`, de modo que `main` permanece disponible para consultar, desplegar o diagnosticar mientras la tarea avanza.
3. La raíz de worktrees es la carpeta hermana del repositorio: `..\AvantIA-worktrees\<slug>`. No crees worktrees dentro del repositorio: `.dockerignore` está vacío y el contexto de construcción de Docker arrastraría esas carpetas.
4. Formato de nombre de rama: `codex/<tipo>/<slug>`. `<tipo>` es uno de `feat`, `fix`, `refactor`, `docs` o `chore`; `<slug>` describe la tarea en minúsculas con palabras separadas por guiones. Ejemplos: `codex/feat/resumen-reservas`, `codex/fix/fechas-estancia`.
5. La rama de tarea **no sigue** a `origin/main` como upstream. Créala con `--no-track` (los scripts ya lo hacen) para que `git status` informe de su propio estado y no del de `main`.
6. Haz commits descriptivos y separados por cambio dentro de la rama. No mezcles tareas distintas en una misma rama.
7. Antes de integrar, actualiza la rama sobre `origin/main` con `git rebase origin/main` y resuelve allí los conflictos. Los conflictos se resuelven en la rama de tarea, nunca en `main`.
8. Antes de integrar hay que pasar la verificación (ver `Verificación` en `README.md`): `pytest` en `backend/` y `npm run build` en `frontend/`. Si la verificación falla, la rama no se integra.
9. La integración en `main` se hace con `git merge --no-ff`, para que cada tarea quede como un punto identificable del historial y se pueda revertir completa. No uses commits directos sobre `main` ni squash que oculte el origen del cambio.
10. Tras integrar y publicar, **elimina la rama y su worktree**, en local y en `origin`. Una rama que ya está en `main` no debe seguir viva: acumularlas hace imposible distinguir el trabajo pendiente del ya integrado.
11. Las ramas que queden obsoletas (su trabajo se rehízo o se superó por cambios posteriores en `main`) se descartan explícitamente, sin fusionar. Fusionar una rama desfasada puede revertir trabajo posterior.
12. `main` es la referencia completa del proyecto: debe contener todo el trabajo realizado hasta el último cambio. Una rama solo se borra cuando su contenido ya está en `main` o cuando se descarta de forma consciente por obsoleta, y en ese segundo caso se archiva antes. Nunca se borra una rama con trabajo vivo sin respaldo.

### Abrir una tarea

Desde la raíz del repositorio, en PowerShell:

```powershell
.\scripts\new-task-worktree.ps1 -Type feat -Slug resumen-reservas
```

Crea la rama `codex/feat/resumen-reservas` desde `origin/main` recién actualizado y su worktree en `..\AvantIA-worktrees\resumen-reservas`. El equivalente manual es:

```powershell
git fetch origin
git worktree add --no-track -b codex/feat/resumen-reservas "..\AvantIA-worktrees\resumen-reservas" origin/main
```

### Cerrar una tarea

Desde la raíz del repositorio, con los cambios ya commiteados en la rama:

```powershell
.\scripts\finish-task-worktree.ps1 -Slug resumen-reservas
```

El script rebasa la rama sobre `origin/main`, ejecuta la verificación, fusiona en `main` con `--no-ff`, publica `main` (previa confirmación) y elimina el worktree y la rama. Si algo falla, se detiene antes de tocar `main`. Opciones: `-SkipChecks` para omitir la verificación, `-NoPush` para integrar sin publicar y `-KeepWorktree` para conservar la carpeta de trabajo.

Al publicar `main` por esta vía no hay PR, así que el webhook de GitHub (que escucha el evento `Pull requests`) no se dispara: el despliegue lo recoge el sondeo de `origin/main` de la tarea programada, como máximo cinco minutos después. Para desplegar en el acto, ejecuta el despliegue manual en el servidor.

### Mantenimiento de ramas

Para revisar qué ramas siguen vivas y cuáles ya están integradas en `main`:

```powershell
.\scripts\clean-merged-branches.ps1
```

Por defecto solo informa. Compara con `origin/main` por contenido, así que también detecta ramas integradas mediante squash o commits directos, que `git branch --merged` no reconoce. Para borrarlas en local y en `origin`, añade `-Delete`.

### Copia de seguridad y recuperación

Antes de borrar nada, `clean-merged-branches.ps1 -Delete` crea en `..\AvantIA-backups` un `bundle` con **todas** las referencias del repositorio y un inventario `rama → commit` con la misma marca de tiempo. Si falla la copia, no se borra ninguna rama. Para recuperar una rama del respaldo:

```powershell
git fetch "..\AvantIA-backups\ramas-<marca>.bundle" <rama>:<rama>
```

Las ramas que se descartan por obsoletas no son recuperables desde `main`, porque su trabajo nunca llegó allí. Antes de borrarlas, archívalas con una etiqueta publicada en `origin`, que mantiene los commits accesibles en GitHub de forma permanente:

```powershell
git tag archivo/<rama> origin/<rama>
git push origin archivo/<rama>
git push origin --delete <rama>
```

Para volver a trabajar sobre una rama archivada: `git switch -c <rama> archivo/<rama>`. Antes de reutilizarla, compara su contenido con `main` (`git diff origin/main archivo/<rama>`): si se archivó fue porque `main` ya había avanzado por otro camino.

Para comprobar en cualquier momento que `main` no se ha dejado trabajo por el camino, el informe de `clean-merged-branches.ps1` debe mostrar cero ramas con trabajo pendiente, salvo las tareas en curso con worktree abierto.

## Despliegue de producción

1. Para activar la automatización una sola vez, inicia sesión en el servidor con la cuenta que usa Docker Desktop, abre PowerShell y ejecuta:

   ```powershell
   Set-Location "C:\PROYECTOS ANTIGRAVITY\AvantIA"
   .\scripts\install-prod-deploy-task.ps1
   ```

   La tarea comprueba `origin/main`, actualiza únicamente si la rama local `main` está limpia y puede avanzar en fast-forward, y aplica el cambio con Docker Compose. Registra su actividad en `%LOCALAPPDATA%\AvantIA\deploy-watch.log`. Si falla una compilación o el despliegue, lo vuelve a intentar en la siguiente comprobación.
   Si se actualiza `scripts/deploy-prod-watch.ps1`, después de incorporar el cambio en el servidor hay que reiniciar la tarea una vez para que cargue el nuevo script: `Stop-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'` y luego `Start-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'`.
   Para configurar el webhook una vez que la nueva versión esté desplegada, establece un secreto aleatorio en `GITHUB_DEPLOY_WEBHOOK_SECRET` dentro del `.env` del servidor, recrea `api` y añade en GitHub (`Settings → Webhooks`) la URL `https://<APP_DOMAIN>/webhooks/github/deploy`, contenido `application/json`, el mismo secreto y el evento `Pull requests`. Conserva la verificación TLS. El endpoint solo acepta PR fusionados con base `main`.
2. Para desplegar manualmente o diagnosticar la tarea, desde `C:\PROYECTOS ANTIGRAVITY\AvantIA` ejecuta:

   ```powershell
   git pull --ff-only origin main
   docker compose -f compose.yaml -f compose.prod.yaml up --build -d
   docker compose -f compose.yaml -f compose.prod.yaml ps
   ```

3. Para desactivar la automatización, ejecuta `Unregister-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'` desde la cuenta que la instaló.

## Auto-refresh en desarrollo local

- En el equipo local de desarrollo, Compose Watch mantiene actualizados los contenedores al editar archivos del host. Requiere Docker Compose 2.22.0 o posterior; la configuración está en `compose.dev.yaml` y solo se aplica al usar ese archivo.
- Para activar el vigilante una sola vez, desde PowerShell en el equipo local (nunca en `DESKTOP-4597Q79`) ejecuta:

  ```powershell
  Set-Location "C:\Users\informatica3\Workspace\AvantIA"
  git switch main
  git pull --ff-only origin main
  .\scripts\install-local-compose-watch-task.ps1
  ```

- La tarea `AvantIA Local Auto Refresh` se ejecuta en la sesión interactiva de Docker Desktop. Vite aplica cambios de frontend al guardarlos; los cambios del backend sincronizan y reinician `api` y `worker`; los cambios de dependencias reconstruyen las imágenes afectadas. Para obtener cambios nuevos de `main` en el checkout local, primero actualiza el repositorio; después el vigilante refresca los contenedores.
- El registro está en `%LOCALAPPDATA%\AvantIA\local-compose-watch.log`. Para desactivar la tarea, ejecuta `Unregister-ScheduledTask -TaskName 'AvantIA Local Auto Refresh'` desde la cuenta que la instaló.

## Secretos y datos de producción

- Configura `.env` y las credenciales directamente en el servidor. Nunca las incluyas en commits ni las pegues en instrucciones, logs o mensajes.
- No elimines los volúmenes de producción ni ejecutes `docker compose down -v` en ese servidor.
- Si el checkout del servidor tiene cambios locales o diverge de `origin/main`, inspecciona la situación antes de actualizar; no fuerces el reset ni sobrescribas datos.
