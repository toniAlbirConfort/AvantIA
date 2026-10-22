# AvantIA

Centro de atención supervisada para consultas de huéspedes. Reúne WhatsApp individual mediante GOWA, correo corporativo mediante IMAP y avisos de prerreserva de Booking. Analiza consultas con Ollama, consulta una instantánea CSV de disponibilidad y prepara borradores. **Nunca envía mensajes al cliente sin aprobación de un operador.**

## Servicios

| Servicio | Función |
| --- | --- |
| `frontend` | Panel React/TypeScript. En producción, Caddy sirve el panel y HTTPS. |
| `api` | FastAPI, autenticación, webhooks, bandeja y aprobación. |
| `worker` | IMAP, análisis, importación de carpeta, alertas y envíos aprobados. |
| `db` | PostgreSQL para conversaciones, cola, disponibilidad y auditoría. |
| `gowa` | Sesión WhatsApp **propia de AvantIA** en un volumen persistente. |
| `migrate`, `bootstrap` | Migraciones y creación inicial del administrador. |

Ollama se configura mediante una URL interna y no se levanta en este Compose. DanIA es solo una referencia de implementación; este proyecto no lee ni modifica sus datos o contenedores.

## Arranque local

1. Copiar `.env.example` a `.env` y cambiar contraseñas, `APP_SECRET` y secreto de webhook. El archivo `.env` está excluido de Git.
2. Ejecutar `docker compose -f compose.yaml config` y `docker compose -f compose.yaml up --build -d`.
3. Abrir `http://localhost:15173` y entrar con `ADMIN_USERNAME` y `ADMIN_PASSWORD`.
4. Comprobar `http://localhost:18100/health` y `docker compose ps`.

El primer sondeo IMAP fija la posición actual del buzón y no importa correo histórico (`IMAP_INITIAL_BACKFILL=0`). Para una prueba controlada con mensajes anteriores, establecer un número positivo de mensajes **antes del primer arranque**. Las respuestas se mantienen en espera hasta que se configuren y prueben SMTP y GOWA.

## Producción

Configurar `APP_DOMAIN`, `PUBLIC_BASE_URL=https://<dominio>` y DNS hacia el VPS. Ejecutar `docker compose -f compose.yaml -f compose.prod.yaml up --build -d`. Caddy gestiona los certificados HTTPS. PostgreSQL no publica puertos. GOWA solo publica su interfaz en `127.0.0.1:3002`; acceder mediante túnel SSH, vincular el número dedicado por QR y configurar `GOWA_DEVICE_ID` con el ID real del dispositivo. No eliminar el volumen `gowa_session`.

La URL de Ollama debe ser accesible **desde el contenedor worker**. `GOWA_INTERNAL_CHAT_ID` indica el grupo o chat interno de avisos. Si está vacío, las tareas siguen visibles en el panel, pero no hay aviso WhatsApp. `MAIL_MESSAGE_URL_TEMPLATE` es opcional: puede contener `{message_id}` para abrir el correo en el webmail corporativo. El ID original siempre queda registrado.

El desplegador debe permitir tráfico entrante 80/443 y restringir el acceso SSH y al túnel GOWA. Crear usuarios individuales desde la sección Usuarios. Los tokens de sesión duran ocho horas.

## CSV de disponibilidad

Cada fila describe un intervalo **[entrada, salida)**. Seis campos son obligatorios: `property_id`, `property_name`, `capacity`, `start_date`, `end_date`, `status`. Fechas ISO (`YYYY-MM-DD`) o `DD/MM/YYYY`; estado `available`/`occupied` o equivalentes admitidos por el importador. El panel permite mapear cabeceras diferentes; la carpeta automática usa `CSV_COLUMN_MAP` como JSON, por ejemplo `{"property_id":"Codigo","start_date":"Desde"}`.

Para declarar una vivienda disponible, el CSV debe cubrir **todo** el intervalo solicitado con filas `available` y no contener un bloqueo `occupied` que se solape. Una fila de ocupación siempre prevalece. La instantánea es completa: cada CSV válido sustituye al anterior como fuente de consulta. Un CSV inválido se registra y conserva la última versión válida. Pasadas seis horas de la última carga válida, AvantIA deja la disponibilidad para comprobación humana. Hay un ejemplo en [samples/availability.csv](samples/availability.csv).

Se puede subir desde Importaciones o copiar a `/data/imports` del volumen `csv_inbox`. El worker renombra los archivos procesados a `.imported` o `.invalid`; deja el resultado y los errores en el panel.

## Operación diaria

- El webhook firmado de GOWA guarda consultas 1 a 1, ignora grupos y mensajes propios y deduplica por ID del proveedor.
- IMAP usa `EXAMINE` y `BODY.PEEK[]`, sin marcar mensajes como leídos. Los emails de los dominios configurados en `BOOKING_SENDER_DOMAINS` crean tareas prioritarias. La clasificación del remitente no confirma la autenticidad de una reserva; un operador debe comprobarla en Avantio.
- Ollama extrae alojamiento, fechas, huéspedes e idioma. El backend valida la disponibilidad y muestra alternativas que cubren las fechas y capacidad. Si faltan datos, falla la IA o el CSV está anticuado, el borrador pide comprobación; no incluye precios.
- El equipo edita y aprueba. El worker envía por el mismo canal. El correo conserva `In-Reply-To` y `References`. Una entrega incierta se detiene hasta que un administrador comprueba el canal y registra «enviado» o «no enviado»; así se evita un reenvío automático duplicado.
- Una consulta que sigue pendiente tras 15 minutos genera **un** aviso adicional al chat interno. Los umbrales se configuran con `OVERDUE_MINUTES` y `AVAILABILITY_MAX_AGE_HOURS`.

## Desarrollo de correo en solo lectura

El modo actual está bloqueado en el código (`READ_ONLY_MODE = True`), sin opción para desactivarlo desde `.env`. IMAP abre el buzón con `EXAMINE` (`select(..., readonly=True)`) y descarga mensajes con `BODY.PEEK[]`; al cerrar la conexión solo hace `LOGOUT`. No marca correos como leídos, no los mueve, borra ni responde. La aplicación guarda copias de los mensajes en su propia base de datos para mostrarlos y clasificarlos.

En este modo, el endpoint de aprobación devuelve `423`, los botones de envío quedan desactivados y los adaptadores SMTP/GOWA rechazan cualquier llamada. El worker puede importar y clasificar emails, pero las alertas WhatsApp y los envíos por correo no se ejecutan, aunque haya credenciales configuradas. GOWA puede mantenerse apagado.

Para conectar el buzón, completar localmente `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX` y `IMAP_PORT` en `.env`; no compartir la contraseña por chat. `IMAP_INITIAL_BACKFILL=0` procesa mensajes nuevos desde el primer sondeo. Para importar mensajes recientes que ya existan, elegir un límite explícito, por ejemplo `50`; también se leerán en modo IMAP de solo lectura. Después, con API y base de datos activos, arrancar solo el lector: `docker compose up -d worker`. Ollama, si se configura, recibe el contenido para generar la clasificación y los borradores.

## API principal

`POST /api/login` entrega un token Bearer; el resto del panel requiere autenticación. `GET /api/conversations` filtra por estado, canal, prioridad y tipo. `GET /api/conversations/{id}` devuelve mensajes, análisis, borrador y auditoría. `PUT /api/conversations/{id}/draft` edita; `POST /api/conversations/{id}/approve` encola un único envío. `POST /api/imports` requiere administrador y recibe `multipart/form-data` con `file` y `mapping` JSON. `GET /api/integrations` muestra configuración y últimos estados. `POST /webhooks/gowa` verifica `X-Hub-Signature-256` antes de registrar el evento.

## Copias y recuperación

Programar una copia diaria de PostgreSQL y guardar el archivo fuera del VPS junto con una política de retención. En producción, usando siempre `docker compose -f compose.yaml -f compose.prod.yaml`:

```sh
docker compose -f compose.yaml -f compose.prod.yaml exec db pg_dump -U avantia -Fc -f /tmp/avantia.dump avantia
docker compose -f compose.yaml -f compose.prod.yaml cp db:/tmp/avantia.dump ./avantia.dump
```

Probar periódicamente la restauración en un **entorno aislado**, con secretos y volúmenes propios. Tras levantar solo `db`, copiar el archivo y restaurar:

```sh
docker compose -f compose.yaml -f compose.prod.yaml cp ./avantia.dump db:/tmp/avantia.dump
docker compose -f compose.yaml -f compose.prod.yaml exec db pg_restore --clean --if-exists --no-owner -U avantia -d avantia /tmp/avantia.dump
```

Después, ejecutar las migraciones y arrancar API, worker y frontend. Guardar por separado los secretos y el volumen GOWA: la base de datos no incluye la sesión WhatsApp. No usar `docker compose down -v` en producción.

## Integración futura con Avantio

`app/avantio.py` define el contrato de consulta y sincronización. El proveedor activo es `csv`. La implementación de `AvantioProvider` queda expresamente pendiente del contrato y credenciales reales; no se activa con `AVAILABILITY_SOURCE=avantio` hasta completarla y probarla. El resto de conversaciones, tareas y aprobaciones no depende del origen de disponibilidad.

## Verificación

Backend: `cd backend && pip install -e '.[dev]' && pytest`. Frontend: `cd frontend && npm install && npm run build`. Infraestructura: `docker compose -f compose.yaml -f compose.prod.yaml config`, levantar un entorno de prueba, comprobar `/health`, recibir un webhook firmado, importar un CSV y completar una aprobación con destinatarios de prueba.
