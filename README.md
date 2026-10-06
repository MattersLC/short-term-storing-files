# Short-term storing files

## Objetivo

Demostrar almacenamiento temporal en Redis de:

- dos archivos
- un texto

referenciados posteriormente mediante un identificador aleatorio.

Los datos se envían **una sola vez**; después el cliente sólo conserva el ID. Redis guarda la
información con TTL: cada uso válido puede renovarlo, un `DELETE` la elimina al instante y, si nadie
la usa, Redis la borra sola al expirar.

> **Advertencia.** En esta PoC basta con conocer el ID para consultar, renovar o borrar un registro.
> En una implementación real, **conocer el ID NO debe ser suficiente** para acceder o utilizar material
> sensible: el recurso tendría que estar asociado al usuario autenticado y a una sesión autorizada.

## Arquitectura

```
Browser / curl
      ↓   HTTP (multipart / JSON)
   Django   (genera el ID, valida, cifra con AES-256-GCM, nunca escribe archivos a disco)
      ↓   redis-py
    Redis   (Hash con ciphertext + nonce + TTL nativo, sin persistencia)
```

No hay base de datos relacional, Celery ni jobs de limpieza: la expiración la hace Redis.

## Flujo

1. El cliente envía archivos + texto (`POST /api/storage/`).
2. Django genera un identificador aleatorio (UUID v4).
3. Django cifra archivos y texto (AES-256-GCM) y Redis almacena sólo el ciphertext con TTL
   (`REDIS_TTL_SECONDS`).
4. El cliente conserva sólo el identificador.
5. Las consultas posteriores utilizan ese identificador (`GET /api/storage/<id>/`).
6. La actividad puede renovar el TTL (`POST /api/storage/<id>/touch/`).
7. `DELETE /api/storage/<id>/` elimina inmediatamente.
8. La inactividad provoca la expiración automática.

```
        upload
          ↓
   Django genera ID
          ↓
   Django cifra (AES-256-GCM)
          ↓
   Redis: ciphertext + nonce, TTL configurable
          │
          ├── GET    → consultar
          ├── TOUCH  → renovar TTL
          ├── DELETE → borrar ahora
          └── TTL    → borrado automático
```

### Identificador

El ID es un UUID v4 (`uuid.uuid4()`): 122 bits aleatorios obtenidos de `os.urandom`. No es
incremental, no deriva de timestamps ni de un hash de los datos, por lo que no es predecible.
Aun así, es sólo un identificador, no una credencial (ver advertencia arriba).

## Cómo ejecutar

Requisitos: Docker y Docker Compose v2.

### Encryption key

`STORAGE_ENCRYPTION_KEY` es **obligatoria**: Base64 de exactamente 32 bytes aleatorios (AES-256).
No hay clave por defecto. Genera la tuya y guárdala sólo en `.env` (ignorado por git):

```bash
cp .env.example .env
```

```bash
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

o, con OpenSSL:

```bash
openssl rand -base64 32
```

Pega el resultado en `.env` como `STORAGE_ENCRYPTION_KEY=<valor>` (el placeholder de
`.env.example` es inválido a propósito). Si falta, `docker compose` se niega a arrancar; si no es
Base64 válido o no mide 32 bytes, Django falla al iniciar con un `ImproperlyConfigured` claro.

Usa siempre la misma clave mientras existan registros: con otra clave no podrán descifrarse.

### Levantar

```bash
docker compose up --build
```

El resto de variables de `.env` son opcionales. Para una demo rápida de expiración:

```bash
REDIS_TTL_SECONDS=20 docker compose up --build
```

## Cómo detener

```bash
docker compose down
```

Redis corre **sin persistencia** (ver más abajo), así que esto destruye cualquier dato temporal
restante. Al volver a levantar los servicios, los registros anteriores ya no existen.

## Cómo probar desde navegador

Abre http://localhost:8000. Es una única página (Django template + JavaScript vanilla) que llama a
la API con `fetch`. El ID vive sólo en memoria de la página.

**Caso A — renovar TTL**

1. Selecciona dos archivos, escribe un texto y pulsa **Store in Redis**.
2. Activa **Refresh status every second** y observa cómo baja **TTL remaining**.
3. Pulsa **Refresh TTL**: el TTL vuelve aproximadamente al valor inicial.

**Caso B — borrado explícito**

1. Almacena datos.
2. Pulsa **Delete now**: se muestra **Status: Deleted / Not found** y se detiene el auto-refresh.

**Caso C — expiración automática**

1. Almacena datos y activa el auto-refresh.
2. No hagas nada. Cuando el TTL llega a cero, Redis borra el registro y la página muestra
   **Expired / Not found**.

## Cómo probar mediante curl

Todos los métodos que modifican estado (`POST`, `DELETE`) requieren token CSRF. Se obtiene la cookie
desde la página principal y se reenvía en la cabecera `X-CSRFToken`:

```bash
curl -s -c cookies.txt -o /dev/null http://localhost:8000/
TOKEN=$(awk '$6=="csrftoken"{print $7}' cookies.txt)
```

### Health

```bash
curl -i http://localhost:8000/health/
```

`200 {"status": "ok", "redis": "ok"}` o `503` si Redis no está disponible.

### POST — almacenar

```bash
curl -X POST -b cookies.txt -H "X-CSRFToken: $TOKEN" \
  -F "file_1=@./file1.bin" \
  -F "file_2=@./file2.txt" \
  -F "text=Texto de prueba" \
  http://localhost:8000/api/storage/
```

`201 {"id": "6ba26d1e-7a2f-41f8-aaa1-7c5ce4a4ae7f", "ttl_seconds": 300}`

Errores: `400` (faltan campos o texto vacío), `403` (CSRF), `413` (archivo mayor que
`MAX_UPLOAD_SIZE_BYTES`), `503` (Redis no disponible).

### GET — consultar

```bash
curl http://localhost:8000/api/storage/<id>/
```

`200` con nombres de archivo, texto y TTL restante (nunca devuelve el contenido de los archivos):

```json
{"id": "<id>", "exists": true, "file_1_name": "file1.bin", "file_2_name": "file2.txt",
 "text": "Texto de prueba", "ttl_remaining_seconds": 287}
```

`404 {"id": "<id>", "exists": false}` si expiró, se borró o nunca existió.

### TOUCH — renovar TTL

```bash
curl -X POST -b cookies.txt -H "X-CSRFToken: $TOKEN" \
  http://localhost:8000/api/storage/<id>/touch/
```

`200 {"id": "<id>", "touched": true, "ttl_seconds": 300, "ttl_remaining_seconds": 300}`

`404 {"id": "<id>", "exists": false}` si la key ya no existe. Usa `EXPIRE`, que sólo actúa sobre
keys existentes: nunca recrea información expirada.

### DELETE — borrar ahora

```bash
curl -X DELETE -b cookies.txt -H "X-CSRFToken: $TOKEN" \
  http://localhost:8000/api/storage/<id>/
```

`200 {"id": "<id>", "deleted": true}` o `404` si no existe. Conceptualmente representa un logout
explícito o el cierre voluntario de una futura sesión de firma.

### Configuración pública de la PoC

```bash
curl http://localhost:8000/api/config/
```

`200 {"redis_ttl_seconds": 300, "max_upload_size_bytes": 5242880}`. Sólo expone estos dos valores;
nunca la secret key, passwords ni configuración interna de Redis.

## Cómo inspeccionar Redis

Cada registro es un único **Hash** con TTL en la key `temporary_storage:<id>`:

| Campo                | Contenido                                            |
|----------------------|------------------------------------------------------|
| `file_1_name`        | nombre original de `file_1` (en claro, metadato)     |
| `file_1_ciphertext`  | `file_1` cifrado + tag GCM de 16 bytes (bytes crudos) |
| `file_1_nonce`       | nonce aleatorio de 12 bytes                          |
| `file_2_name`        | nombre original de `file_2` (en claro, metadato)     |
| `file_2_ciphertext`  | `file_2` cifrado + tag GCM                           |
| `file_2_nonce`       | nonce aleatorio de 12 bytes                          |
| `text_ciphertext`    | texto (UTF-8) cifrado + tag GCM                      |
| `text_nonce`         | nonce aleatorio de 12 bytes                          |
| `encryption_version` | formato criptográfico (`v1`)                         |

```bash
# Listar keys de la PoC
docker compose exec redis redis-cli KEYS "temporary_storage:*"

# TTL restante en segundos (-2 = la key ya no existe)
docker compose exec redis redis-cli TTL "temporary_storage:<id>"

# Tipo de dato (hash)
docker compose exec redis redis-cli TYPE "temporary_storage:<id>"

# Sólo metadatos: campos, nombres de archivo y tamaño en bytes de cada contenido
docker compose exec redis redis-cli HKEYS "temporary_storage:<id>"
docker compose exec redis redis-cli HMGET "temporary_storage:<id>" file_1_name file_2_name
docker compose exec redis redis-cli HLEN "temporary_storage:<id>"
docker compose exec redis redis-cli HMGET "temporary_storage:<id>" encryption_version
docker compose exec redis redis-cli HSTRLEN "temporary_storage:<id>" file_1_ciphertext
docker compose exec redis redis-cli HSTRLEN "temporary_storage:<id>" text_nonce
```

(`KEYS` es aceptable aquí por ser una PoC con pocas keys; en producción se usaría `SCAN`.)

### Comprobar que Redis no contiene plaintext

1. Crea dos archivos reconocibles y almacénalos (desde la UI o con curl) junto con un texto
   reconocible:

   ```bash
   printf SUPER_SECRET_FILE_ONE > file1.txt
   ```

   ```bash
   printf SUPER_SECRET_FILE_TWO > file2.txt
   ```

   Texto: `SUPER_SECRET_TEXT`.

2. Lista los campos: sólo deben aparecer `*_ciphertext`, `*_nonce`, `*_name` y
   `encryption_version` (ningún campo con el contenido original):

   ```bash
   docker compose exec redis redis-cli HKEYS "temporary_storage:<id>"
   ```

3. Busca los secretos en el registro completo. El resultado debe ser `0`:

   ```bash
   docker compose exec -T redis redis-cli HGETALL "temporary_storage:<id>" | grep -ac SUPER_SECRET
   ```

   `HGETALL` sin filtrar muestra los ciphertext como bytes ilegibles (`\x8f\x1c...`); el texto
   original no es visible. Para archivos grandes evita imprimirlo completo y usa `HSTRLEN`: el
   ciphertext mide exactamente el tamaño original + 16 bytes (tag GCM).

4. Comprueba que la aplicación sí descifra: `GET /api/storage/<id>/` devuelve `SUPER_SECRET_TEXT`.

## Persistencia de Redis

Esta PoC representa almacenamiento **efímero**, así que Redis se configura en
`docker-compose.yml` para no persistir nada:

- `--appendonly no`: AOF desactivado.
- `--save ""`: snapshots RDB desactivados.
- `/data` montado como `tmpfs`: sin volumen persistente (ni siquiera el volumen anónimo que declara
  la imagen oficial).

Resultado: `docker compose down` destruye cualquier información temporal restante.

Es una decisión **específica de la PoC**, no una configuración suficiente para producción (donde
habría que considerar autenticación de Redis, TLS, red aislada, límites de memoria, política de
expulsión, alta disponibilidad, etc.).

## Variables de entorno

| Variable                | Por defecto | Descripción                                        |
|-------------------------|-------------|----------------------------------------------------|
| `REDIS_HOST`            | `redis`     | Host de Redis                                      |
| `REDIS_PORT`            | `6379`      | Puerto de Redis                                    |
| `REDIS_TTL_SECONDS`     | `300`       | TTL al crear y al renovar (`touch`) un registro    |
| `MAX_UPLOAD_SIZE_BYTES` | `5242880`   | Tamaño máximo por archivo (5 MB)                   |
| `STORAGE_ENCRYPTION_KEY`| (ninguno)   | **Obligatoria.** Base64 de 32 bytes (AES-256)      |
| `DJANGO_SECRET_KEY`     | (dev key)   | Clave secreta de Django; cámbiala fuera de la demo |
| `DJANGO_DEBUG`          | `1`         | Modo debug (`1`/`0`)                               |

## Tests

Requieren Redis, así que se ejecutan dentro del contenedor:

```bash
docker compose exec web python manage.py test
```

Cubren: almacenamiento (incluye bytes binarios), validaciones 400/413, GET 200/404, TTL configurado,
renovación con `touch`, `touch` sobre ID inexistente, `DELETE` y GET posterior, CSRF, `/api/config/`
y una **expiración real** con TTL de 1 s (comprueba que la key desaparece de Redis y que `touch`
no la recrea).

Cifrado: Redis no contiene el plaintext de archivos ni texto; los valores descifran exactamente al
original; el mismo input produce ciphertext distinto (nonces aleatorios); modificar un byte del
ciphertext o del tag, usar otro nonce, el AAD de otro ID u otro campo, o una versión desconocida
provoca fallo de autenticación (`GET` responde `500 storage_integrity_error` sin filtrar datos);
TTL, `touch` (sin modificar el ciphertext) y `DELETE` siguen funcionando; validación de
`STORAGE_ENCRYPTION_KEY` (ausente, Base64 inválido, longitud distinta de 32 bytes).

## Qué demuestra esta PoC

- Almacenamiento binario en Redis (archivos sin base64, nunca escritos a disco).
- Cifrado a nivel aplicación (AES-256-GCM) antes de Redis: Redis sólo ve ciphertext.
- TTL nativo de Redis.
- Renovación de TTL con cada uso explícito (`touch`).
- Borrado explícito e inmediato (`DELETE`).
- Acceso posterior mediante un ID aleatorio, sin reenviar los datos.
- Expiración automática por inactividad.
- Reproducibilidad completa mediante Docker.

## Qué NO demuestra

- No hay autenticación.
- No hay JWT.
- No hay autorización: cualquiera que conozca el ID puede usarlo.
- No hay gestión de claves productiva (KMS/HSM/Vault, rotación): ver más abajo.
- No hay firma digital.
- No hay validación de `.cer`.
- No hay validación de `.key`.
- No hay manejo de password de e.firma.
- No es una arquitectura productiva (servidor de desarrollo de Django, `ALLOWED_HOSTS = ["*"]`,
  Redis sin password ni TLS).
- No valida cumplimiento normativo.

## Encryption model

```
Cliente
   ↓  archivos + texto en claro (HTTP)
Django
   ↓  AES-256-GCM  (clave: STORAGE_ENCRYPTION_KEY, nonce aleatorio por valor, AAD = ID)
Redis
      ciphertext + nonce + metadatos mínimos + TTL
```

Al recuperar, Django lee el ciphertext, lo descifra y el plaintext sólo existe dentro del proceso
Django. El cifrado vive en `storage/crypto.py` (`encrypt` / `decrypt`); las views sólo deciden qué
campos cifrar y con qué AAD.

- **Redis nunca recibe plaintext** del contenido de los archivos ni del texto. Sólo quedan en claro
  el ID (en la key), los nombres de archivo y `encryption_version`.
- **La clave AES vive fuera de Redis**: llega a Django sólo por variable de entorno; nunca está en
  el código, el Dockerfile, `docker-compose.yml` ni Redis.
- **AES-GCM aporta confidencialidad e integridad**: cualquier modificación del ciphertext, del
  nonce o del AAD hace fallar la verificación del tag y no se devuelve nada. La app responde un
  error genérico y sólo registra `Encrypted storage payload authentication failed`.
- **Cada valor usa un nonce aleatorio** de 12 bytes (`os.urandom(12)`), así que el mismo archivo
  cifrado dos veces produce ciphertext distinto.
- **El ID del registro se usa como AAD**: `v1|temporary_storage:<uuid>|<campo>`. Copiar un
  ciphertext a otro registro (o a otro campo del mismo registro) rompe la autenticación.
- **TTL y cifrado resuelven problemas distintos**: el TTL limita *cuánto tiempo* existen los datos;
  el cifrado limita *quién puede leerlos* mientras existen. `touch` sólo renueva el TTL, sin
  descifrar ni recifrar.
- `encryption_version = "v1"` permite cambiar de estrategia criptográfica en el futuro sin asumir
  que todos los registros usan el mismo formato (hoy sólo existe `v1`).
- **Memoria de Python**: Python no ofrece garantías fuertes de borrado (zeroization) de memoria;
  el plaintext y la clave permanecen en memoria del proceso hasta que el recolector la libera y se
  reutiliza. La PoC no intenta simularlo; es una limitación conocida.

## Important production limitation

En esta PoC la **clave maestra vive como variable de entorno**. En producción esto debería
evolucionar hacia:

- envelope encryption (una data key por registro, cifrada por una clave maestra),
- KMS / HSM / Vault para custodiar la clave maestra,
- rotación de claves,
- controles de acceso estrictos y auditoría sobre quién puede usar la clave.

Quien comprometa simultáneamente **Django**, **`STORAGE_ENCRYPTION_KEY`** y **Redis** podría
descifrar los datos. El cifrado protege especialmente contra un compromiso aislado de Redis, sus
backups o un acceso accidental al datastore.

## Evolución futura

Conceptualmente (no implementado), el mismo patrón podría sostener una sesión de firma:

```
.cer + .key + password
        ↓
creación de signing session   (usuario autenticado, validación de certificado)
        ↓
Redis con TTL                 (material cifrado, asociado a usuario + sesión)
        ↓
signing_session_id
        ↓
requests posteriores usan ese ID (+ autenticación), renuevan TTL al firmar
y lo eliminan con logout explícito o por inactividad
```
