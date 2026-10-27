# Instrucciones de trabajo para AvantIA

## Servidor de producción e integración

- El servidor local que hace de producción y punto manual de integración/despliegue es `DESKTOP-4597Q79`, accesible por SSH como `informatica2@192.168.0.158`.
- La carpeta contenedora en el servidor es `C:\PROYECTOS ANTIGRAVITY`; la raíz del clon de este repositorio es `C:\PROYECTOS ANTIGRAVITY\AvantIA`.
- El remoto canónico es `https://github.com/toniAlbirConfort/AvantIA.git` y la rama de despliegue es `main` (`origin/main`).
- Este flujo es manual; no hay un pipeline automatizado configurado en el repositorio.

## Flujo de cambios y despliegue

1. Implementa cada cambio solicitado en el checkout de desarrollo y crea un commit separado por cambio, con un mensaje descriptivo.
2. Sube los commits aprobados a `origin/main`.
3. En el servidor, desde `C:\PROYECTOS ANTIGRAVITY\AvantIA`, incorpora los cambios con `git pull origin main`.
4. Para aplicar la versión actualizada, ejecuta desde esa misma carpeta:

   ```powershell
   docker compose -f compose.yaml -f compose.prod.yaml up --build -d
   ```

5. Comprueba el estado de los servicios con `docker compose -f compose.yaml -f compose.prod.yaml ps`.

## Secretos y datos de producción

- Configura `.env` y las credenciales directamente en el servidor. Nunca las incluyas en commits ni las pegues en instrucciones, logs o mensajes.
- No elimines los volúmenes de producción ni ejecutes `docker compose down -v` en ese servidor.
- Si el checkout del servidor tiene cambios locales o diverge de `origin/main`, inspecciona la situación antes de actualizar; no fuerces el reset ni sobrescribas datos.
