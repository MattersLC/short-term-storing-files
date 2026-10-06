# short-term-storing-files

Prueba de concepto para almacenar archivos de forma temporal (TTL) en Redis usando Django.
Esta primera etapa solo deja la infraestructura base: Django + Redis en Docker Compose y un endpoint de salud.

## Requisitos

- Docker
- Docker Compose (v2, comando `docker compose`)

## Levantar el proyecto

```bash
cp .env.example .env   # opcional; sin .env se usan valores por defecto
docker compose up --build
```

La aplicación queda disponible en http://localhost:8000.

## Probar /health/

```bash
curl -i http://localhost:8000/health/
```

Respuesta esperada (HTTP 200):

```json
{"status": "ok", "redis": "ok"}
```

Si Redis no está disponible, responde HTTP 503 con `"status": "error"` y `"redis": "unavailable"`.

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
