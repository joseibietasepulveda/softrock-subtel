# Configuración por ambiente

## PostgreSQL e instalación inicial

SUBTEL provisiona una base PostgreSQL independiente para DEV y otra para PROD. La imagen no contiene un servidor PostgreSQL; Compose incorpora uno solo para instalación local. Usar PostgreSQL 16 como referencia de despliegue.

Configurar `DATABASE_URL` mediante un Secret o el mecanismo de secretos institucional. La cuenta necesita conectar y crear/administrar las tablas, secuencias, funciones y triggers del esquema usado por la aplicación. La inicialización se ejecuta al arrancar y está serializada con un bloqueo PostgreSQL. Una cuenta limitada únicamente a SELECT/INSERT/UPDATE no basta para esta inicialización.

Para una base nueva, definir `ADMIN_PASSWORD`; crea el usuario `admin` si no existen usuarios. Se exige un mínimo de 10 caracteres, una mayúscula y un número. `REGULAR_PASSWORD` es opcional y crea `regular` solo cuando se proporciona. Las variables no cambian la contraseña de cuentas ya existentes: para ello se utiliza la administración de usuarios o «Mi cuenta».

Una conexión tiene la forma `postgresql://USUARIO:CONTRASEÑA@HOST:5432/BASE`. Codificar los caracteres reservados de la contraseña cuando se incorporan a una URL. Configurar TLS según los requisitos del servicio PostgreSQL de SUBTEL; por ejemplo, parámetros `sslmode` y certificados de confianza suministrados por infraestructura.

`MIGRATE_SQLITE_PATH` corresponde exclusivamente a importaciones desde una instalación SQLite anterior. Se omite en instalaciones nuevas de SUBTEL; no se requiere una copia de nuestra base de datos.

## Variables principales

| Variable | Valor / comportamiento | Destino |
|---|---|---|
| `DATABASE_URL` | Obligatoria; sin conexión incorporada por defecto | Secret |
| `ADMIN_PASSWORD` | Obligatoria para inicializar una base sin usuarios | Secret |
| `REGULAR_PASSWORD` | Opcional; vacía no crea usuario Regular | Secret |
| `DATA_DIR` | `/data` en la imagen; `./data` en Python local | ConfigMap + volumen |
| `PORT` | 8000 | ConfigMap |
| `OCR_LANG` | Se selecciona español/inglés disponibles; ejemplo `spa+eng` | ConfigMap |
| `EXPLICIT_RULES_FILE` | Ruta a un JSON privado; vacía usa detección general | ConfigMap con la ruta + archivo privado montado, si aplica |

## Capacidad y tamaño

| Variable | Valor del código o imagen | Significado |
|---|---:|---|
| `WEB_WORKERS` | 1 en la imagen de entrega | Procesos web |
| `PROCESSING_WORKERS` | 2 | Hilos de documentos por proceso web |
| `JOB_WORKERS` | 1 | Hilos masivos por proceso web; 0 deshabilita esos hilos |
| `MAX_UPLOAD_MB` | 200 | Tamaño por archivo |
| `MAX_REQUEST_MB` | 500 | Límite total de carga interactiva, incluido ZIP expandido |
| `MAX_FILES_PER_REQUEST` | 200 | Archivos por carga interactiva |
| `MAX_PAGES` | 500 | Páginas por documento; 0 elimina este límite |
| `MAX_QUEUE` | 150 | Umbral de cola interactiva |
| `MIN_FREE_DISK_MB` | 500 | Umbral de espacio libre de `/data` |
| `RETENTION_DAYS_ORIGINAL` | 30 | Retención de originales según estado/fecha de protección |
| `RETENTION_DAYS_PROTECTED` | 365 | Retención de copias protegidas según estado/fecha de protección |
| `JOB_LEASE_SECONDS` | 1800 | Plazo para completar un ítem masivo antes de que otro proceso pueda reclamarlo |
| `JOB_POLL_SECONDS` | 2 | Intervalo de consulta de la cola masiva |
| `MAX_JOB_ITEMS` | 1000 | Ítems por solicitud masiva |
| `MAX_JOB_QUEUE` | 100000 | Umbral global de ítems masivos activos |

La capacidad del PVC, el almacenamiento temporal y las cuotas de CPU/RAM deben ajustarse con mediciones. Un PDF escaneado de pocas megas puede producir muchas imágenes y consumir bastante más disco y memoria al procesarse. El chequeo de salud mide el espacio de `/data`; supervisar también el almacenamiento temporal del contenedor.

## Autenticación institucional

| Variable | Uso |
|---|---|
| `OIDC_ISSUER` | URL del realm Keycloak |
| `OIDC_CLIENT_ID` | Identificador del cliente |
| `OIDC_CLIENT_SECRET` | Secreto del cliente confidencial, si aplica |
| `OIDC_REDIRECT_URI` | URL pública exacta terminada en `/api/auth/oidc/callback` |
| `OIDC_ROLE_MAP` | JSON que relaciona roles de Keycloak con perfiles de la aplicación |
| `OIDC_DEFAULT_ROLE` | Perfil por defecto opcional; vacío exige un rol reconocido |
| `OIDC_LABEL` | Texto del botón de ingreso |

Se habilita el botón institucional cuando existen `OIDC_ISSUER` y `OIDC_CLIENT_ID`. Las cuentas Google se federan en Keycloak; la aplicación no necesita claves de Google. [Procedimiento de integración](INTEGRACIONES.md#keycloak-y-cuentas-institucionales).

LDAP es opcional mediante `LDAP_ENABLED`, `LDAP_URL`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_BASE_DN`, `LDAP_USER_ATTR`, `LDAP_GROUP_ROLE_MAP` y `LDAP_DEFAULT_ROLE`. No es necesario configurarlo si se usa Keycloak.

## Buckets

`BUCKET_ALLOWED_HOSTS` contiene una lista de nombres de host separados por comas, sin esquema ni ruta. Debe incluir tanto los hosts de lectura como los de escritura. Vacía impide aceptar trabajos masivos. Utilizar HTTPS en los ambientes institucionales y restringir la salida de red a los servicios requeridos.

## Aplicación de cambios

Las variables se leen al iniciar la aplicación o durante cada operación según el módulo. Reiniciar el Deployment después de cambiar la configuración o el perfil de reglas para aplicar una versión consistente en todos los procesos. Las credenciales se mantienen fuera de Git y se administran por separado en DEV y PROD.
