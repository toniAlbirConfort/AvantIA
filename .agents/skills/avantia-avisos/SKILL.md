---
name: avantia-avisos
description: Evalúa en cada implementación nueva de AvantIA si genera eventos que deban notificarse y pregunta antes de añadirlos al sistema de avisos.
---

# Avisos en AvantIA

Aplica esta norma al diseñar o implementar cualquier funcionalidad, integración, trabajo del worker, importación o cambio de estado en AvantIA. El sistema de avisos vive en `backend/app/notifications.py` y se muestra en la vista **Avisos** (`frontend/src/Notifications.tsx`).

## Cuándo evaluar

Antes de dar por cerrado el diseño de un cambio, revisa si introduce alguno de estos casos:

- Un fallo que hoy solo se vería en los logs: excepciones capturadas, `last_error`, reintentos agotados.
- Una integración externa que puede dejar de funcionar: IMAP, SMTP, GOWA, Avantio, Ollama, Chrome.
- Un proceso en segundo plano o programado cuyo fin, fallo o retraso importe a alguien.
- Un estado que exige una acción humana: revisar, confirmar, resolver, registrar.
- Un umbral de tiempo o de datos: algo pendiente demasiado tiempo, datos desactualizados.
- Una acción irreversible o sensible de administración.

## Qué hacer

1. Si el cambio encaja en algún caso, **pregunta al usuario antes de implementarlo** si debe generar un aviso. Propón:
   - el `kind`;
   - la severidad: `critical`, `action` o `info`;
   - la audiencia: `all` o `admin`;
   - la clave de deduplicación;
   - si es una condición (se resuelve sola cuando desaparece la causa) o un evento (se acumulan ocurrencias);
   - cuándo se cierra.
2. No añadas avisos sin confirmación ni actives su envío externo (WhatsApp, correo). Cualquier envío externo empieza siempre en modo `shadow`.
3. Si se aprueba:
   - Declara el tipo en `KINDS` de `app/notifications.py`. Un tipo no declarado lanza error.
   - Usa `raise_condition()` y `clear_condition()` para estados, preferiblemente desde un vigilante en `WATCHERS` que lea el estado de la base de datos. Usa `record_event()` para hechos puntuales. No crees mecanismos de alerta paralelos.
   - Añade tests que comprueben que el aviso se crea, que no se duplica y que se resuelve.
   - Documenta el aviso nuevo en la tabla «Avisos» del `README.md`.
4. Si no se aprueba, deja constancia en la descripción del PR o del commit con el motivo, por ejemplo «sin aviso: …».

## Contenido de los avisos

- Títulos breves, en castellano y accionables, con un enlace (`link`) a la vista donde se resuelve: `/inbox/<id>`, `/bookings/<id>`, `/integrations`, `/imports`…
- Sin datos personales completos, datos de pago, tokens, contraseñas ni rutas internas. Referencia los objetos por su ID.
- Un único aviso activo por episodio: si se repite, se actualiza o suma ocurrencias en lugar de duplicarse.
- Todo aviso cuyo problema puede desaparecer debe resolverse solo cuando desaparezca. Un aviso descartado no reaparece hasta que su causa se resuelve.
- Los avisos de sistema (infraestructura, integraciones, trabajos) van a `admin`; los operativos que cualquiera puede atender van a `all`.
