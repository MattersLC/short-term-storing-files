# short-term-storing-files

Prueba de concepto para almacenar archivos de forma temporal (TTL) en Redis usando Django.
Incluye Django + Redis en Docker Compose, un endpoint de salud, un endpoint para guardar temporalmente dos archivos y un texto en Redis con TTL nativo, y una interfaz web mínima para probarlo manualmente.

## Requisitos

- Docker
- Docker Compose (v2, comando `docker compose`)

## Levantar el proyecto

```bash
cp .env.example .env   # opcional; sin .env se usan valores por defecto
docker compose up --build
```

La aplicación queda disponible en http://localhost:8000.

## Interfaz web de prueba

Abre http://localhost:8000 en el navegador. Es una única página (Django template + HTML, CSS y
JavaScript vanilla, sin frameworks ni npm) que llama a la API con `fetch` y `FormData`.

### Flujo de prueba recomendado

1. Selecciona dos archivos (cualquier tipo) en **File 1** y **File 2**.
2. Escribe un texto.
3. Pulsa **Store in Redis**: se muestran el ID temporal y el TTL configurado.
4. Activa **Refresh status every second**.
5. Observa cómo disminuye **TTL remaining** en cada consulta.
6. Espera a que el TTL llegue a cero y Redis elimine el registro.
7. La interfaz recibe un 404, detiene el auto-refresh y muestra **Expired / Not found**.

Para no esperar 5 minutos, levanta el servicio con un TTL corto:

```bash
REDIS_TTL_SECONDS=20 docker compose up -d web
```

El navegador sólo actúa como cliente de prueba: no guarda nada (el ID vive en memoria de la
página) ni calcula la expiración. El TTL lo fija el backend y la eliminación la hace Redis; la
página se limita a consultar `GET /api/storage/<id>/` y mostrar lo que responde.

### CSRF

La protección CSRF de Django está activa (`CsrfViewMiddleware`). La página obtiene el token con
`{% csrf_token %}` (y la cookie `csrftoken`) y lo envía en la cabecera `X-CSRFToken` del POST.
Un POST sin token válido responde HTTP 403 con `{"error": "csrf_failed", ...}`.

## Probar /health/

```bash
curl -i http://localhost:8000/health/
```

Respuesta esperada (HTTP 200):

```json
{"status": "ok", "redis": "ok"}
```

Si Redis no está disponible, responde HTTP 503 con `"status": "error"` y `"redis": "unavailable"`.

## Almacenamiento temporal

### Guardar dos archivos y un texto

`POST /api/storage/` (multipart/form-data) con los campos obligatorios `file_1`, `file_2` y `text`.
Requiere token CSRF: primero se obtiene la cookie desde la página principal y se reenvía en la cabecera.

```bash
curl -s -c cookies.txt -o /dev/null http://localhost:8000/
TOKEN=$(awk '$6=="csrftoken"{print $7}' cookies.txt)
curl -X POST -b cookies.txt -H "X-CSRFToken: $TOKEN" \
  -F "file_1=@./file1.txt" \
  -F "file_2=@./file2.txt" \
  -F "text=Texto de prueba" \
  http://localhost:8000/api/storage/
```

Respuesta (HTTP 201):

```json
{"id": "6ba26d1e-7a2f-41f8-aaa1-7c5ce4a4ae7f", "ttl_seconds": 300}
```

Errores:

- HTTP 400: falta algún campo o `text` está vacío (`"fields"` indica cuáles).
- HTTP 403: falta el token CSRF o no es válido.
- HTTP 413: algún archivo supera `MAX_UPLOAD_SIZE_BYTES`.
- HTTP 503: Redis no disponible.

### Consultar si el registro sigue existiendo

```bash
curl http://localhost:8000/api/storage/<id>/
```

Si existe (HTTP 200), devuelve nombres de archivos, texto y TTL restante leído de Redis
(no devuelve el contenido de los archivos):

```json
{"id": "<id>", "exists": true, "file_1_name": "file1.txt", "file_2_name": "file2.txt",
 "text": "Texto de prueba", "ttl_remaining_seconds": 287}
```

Si expiró o no existe (HTTP 404):

```json
{"id": "<id>", "exists": false}
```

### Estructura en Redis

Cada registro es un único **Hash** con TTL en la key `temporary_storage:<uuid>`:

| Campo            | Contenido                         |
|------------------|-----------------------------------|
| `file_1_name`    | nombre original de `file_1`       |
| `file_1_content` | bytes de `file_1` (sin base64)    |
| `file_2_name`    | nombre original de `file_2`       |
| `file_2_content` | bytes de `file_2` (sin base64)    |
| `text`           | texto recibido                    |

Los archivos nunca se escriben en disco: se reciben en memoria y se envían directo a Redis.
La expiración la hace Redis (`EXPIRE`); no hay jobs de limpieza.

### Inspeccionar Redis desde Docker

```bash
docker compose exec redis redis-cli KEYS 'temporary_storage:*'
docker compose exec redis redis-cli TTL temporary_storage:<id>
docker compose exec redis redis-cli HKEYS temporary_storage:<id>
docker compose exec redis redis-cli HGET temporary_storage:<id> text
```

`TTL` devuelve los segundos restantes; `-2` significa que la key ya no existe.

### Probar la expiración manualmente con un TTL corto

```bash
REDIS_TTL_SECONDS=10 docker compose up -d web
```

Haz el POST, consulta el GET (200), espera más de 10 segundos y vuelve a consultar: responde 404
y `redis-cli EXISTS temporary_storage:<id>` devuelve `0`. Para volver al valor por defecto:
`docker compose up -d web`.

## Tests

Requieren Redis, así que se ejecutan dentro del contenedor:

```bash
docker compose exec web python manage.py test
```

## Detener los contenedores

```bash
docker compose down
```

## Variables de entorno

| Variable            | Por defecto | Descripción                          |
|---------------------|-------------|--------------------------------------|
| `DJANGO_SECRET_KEY` | (dev key)   | Clave secreta de Django              |
| `DJANGO_DEBUG`      | `1`         | Modo debug (`1`/`0`)                 |
| `REDIS_HOST`        | `redis`     | Host de Redis                        |
| `REDIS_PORT`        | `6379`      | Puerto de Redis                      |
| `REDIS_TTL_SECONDS` | `300`       | TTL (segundos) para datos temporales |
| `MAX_UPLOAD_SIZE_BYTES` | `5242880` | Tamaño máximo por archivo (5 MB)  |
