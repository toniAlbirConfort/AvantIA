# Instrucciones de trabajo para AvantIA

## Servidor de producción e integración

- El servidor local que hace de producción y punto manual de integración/despliegue es `DESKTOP-4597Q79`, accesible por SSH como `informatica2@192.168.0.158`.
- La carpeta contenedora en el servidor es `C:\PROYECTOS ANTIGRAVITY`; la raíz del clon de este repositorio es `C:\PROYECTOS ANTIGRAVITY\AvantIA`.
- El remoto canónico es `https://github.com/toniAlbirConfort/AvantIA.git` y la rama de despliegue es `main` (`origin/main`).
- Este flujo es manual; no hay un pipeline automatizado configurado en el repositorio.

## Flujo de cambios y despliegue

1. Cada tarea que implique cambios debe implementarse en una rama nueva y exclusiva para esa tarea. No reutilices ramas de otras tareas ni trabajes directamente sobre `main`.
2. Antes de crear la rama, actualiza las referencias remotas con `git fetch origin`. La base de toda rama de trabajo será siempre la referencia remota `origin/main`, nunca una rama local desactualizada ni otra rama de trabajo.
3. Usa este formato común para los nombres de rama: `codex/<tipo>/<slug>`. `<tipo>` debe ser uno de `feat`, `fix`, `refactor`, `docs` o `chore`; `<slug>` debe describir brevemente la tarea en minúsculas y con palabras separadas por guiones. Por ejemplo: `codex/feat/resumen-reservas` o `codex/fix/fechas-estancia`.
4. Crea la rama nueva desde `origin/main` (por ejemplo, `git switch --create codex/fix/fechas-estancia origin/main`) e implementa allí los cambios. Crea commits descriptivos y separados por cambio dentro de la rama de la tarea.
5. Publica la rama de tarea en `origin` para revisión. Tras su aprobación, integra sus commits en `origin/main` mediante el mecanismo de integración aprobado; no publiques directamente los commits de trabajo sobre `main`.
6. El despliegue siempre se actualiza desde `origin/main`. En el servidor, desde `C:\PROYECTOS ANTIGRAVITY\AvantIA`, incorpora los cambios ya integrados con `git pull origin main`.
7. Para aplicar la versión actualizada, ejecuta desde esa misma carpeta:

   ```powershell
   docker compose -f compose.yaml -f compose.prod.yaml up --build -d
   ```

8. Comprueba el estado de los servicios con `docker compose -f compose.yaml -f compose.prod.yaml ps`.

## Secretos y datos de producción

- Configura `.env` y las credenciales directamente en el servidor. Nunca las incluyas en commits ni las pegues en instrucciones, logs o mensajes.
- No elimines los volúmenes de producción ni ejecutes `docker compose down -v` en ese servidor.
- Si el checkout del servidor tiene cambios locales o diverge de `origin/main`, inspecciona la situación antes de actualizar; no fuerces el reset ni sobrescribas datos.
