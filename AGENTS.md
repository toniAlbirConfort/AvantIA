# Instrucciones de trabajo para AvantIA

## Servidor de producción e integración

- El servidor local de producción es `DESKTOP-4597Q79`, accesible por SSH como `informatica2@192.168.0.158`.
- La carpeta contenedora en el servidor es `C:\PROYECTOS ANTIGRAVITY`; la raíz del clon de este repositorio es `C:\PROYECTOS ANTIGRAVITY\AvantIA`.
- El remoto canónico es `https://github.com/toniAlbirConfort/AvantIA.git` y la rama de despliegue es `main` (`origin/main`).
- El despliegue se automatiza con la tarea programada `AvantIA Production Auto Deploy`. Esta consulta `origin/main` cada cinco minutos mientras la cuenta que ejecuta Docker Desktop está conectada.
- La tarea debe ejecutarse en la sesión interactiva de esa cuenta, porque Docker Desktop y su almacén de credenciales están vinculados a ella. No se debe configurar como una tarea que se ejecute aunque el usuario no haya iniciado sesión.

## Flujo de cambios y despliegue

1. Cada tarea que implique cambios debe implementarse en una rama nueva y exclusiva para esa tarea. No reutilices ramas de otras tareas ni trabajes directamente sobre `main`.
2. Antes de crear la rama, actualiza las referencias remotas con `git fetch origin`. La base de toda rama de trabajo será siempre la referencia remota `origin/main`, nunca una rama local desactualizada ni otra rama de trabajo.
3. Usa este formato común para los nombres de rama: `codex/<tipo>/<slug>`. `<tipo>` debe ser uno de `feat`, `fix`, `refactor`, `docs` o `chore`; `<slug>` debe describir brevemente la tarea en minúsculas y con palabras separadas por guiones. Por ejemplo: `codex/feat/resumen-reservas` o `codex/fix/fechas-estancia`.
4. Crea la rama nueva desde `origin/main` (por ejemplo, `git switch --create codex/fix/fechas-estancia origin/main`) e implementa allí los cambios. Crea commits descriptivos y separados por cambio dentro de la rama de la tarea.
5. Publica la rama de tarea en `origin` para revisión. Tras su aprobación, integra sus commits en `origin/main` mediante el mecanismo de integración aprobado; no publiques directamente los commits de trabajo sobre `main`.
6. Para activar la automatización una sola vez, inicia sesión en el servidor con la cuenta que usa Docker Desktop, abre PowerShell y ejecuta:

   ```powershell
   Set-Location "C:\PROYECTOS ANTIGRAVITY\AvantIA"
   .\scripts\install-prod-deploy-task.ps1
   ```

   La tarea comprueba `origin/main`, actualiza únicamente si la rama local `main` está limpia y puede avanzar en fast-forward, y aplica el cambio con Docker Compose. Registra su actividad en `%LOCALAPPDATA%\AvantIA\deploy-watch.log`. Si falla una compilación o el despliegue, lo vuelve a intentar en la siguiente comprobación.
   Si se actualiza `scripts/deploy-prod-watch.ps1`, después de incorporar el cambio en el servidor hay que reiniciar la tarea una vez para que cargue el nuevo script: `Stop-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'` y luego `Start-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'`.
7. Para desplegar manualmente o diagnosticar la tarea, desde `C:\PROYECTOS ANTIGRAVITY\AvantIA` ejecuta:

   ```powershell
   git pull --ff-only origin main
   docker compose -f compose.yaml -f compose.prod.yaml up --build -d
   docker compose -f compose.yaml -f compose.prod.yaml ps
   ```

8. Para desactivar la automatización, ejecuta `Unregister-ScheduledTask -TaskName 'AvantIA Production Auto Deploy'` desde la cuenta que la instaló.

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
