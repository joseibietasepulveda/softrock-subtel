# Operación, validación y pruebas

## Validación de instalación

Realizar en DEV con documentos de prueba:

1. Consultar `/api/health`: debe responder HTTP 200 con base y OCR disponibles.
2. Ingresar con el administrador inicial, comprobar los roles y crear los usuarios necesarios.
3. Cargar un TXT y un PDF de prueba; esperar el estado de revisión.
4. Revisar y editar hallazgos, elegir un tratamiento y generar la copia protegida.
5. Descargar y comprobar visualmente los datos tratados y cualquier advertencia.
6. Repetir con un usuario Regular: la descarga requiere aprobación administrativa.
7. Reiniciar el pod y comprobar que se mantienen usuarios, documentos y copias gracias a PostgreSQL y al PVC.
8. Consultar la bitácora y `/api/audit/verify` con una cuenta autorizada.
9. Si se habilita Keycloak, probar ingreso, rechazo por falta de rol y cierre de sesión.
10. Si se habilitan buckets, enviar un trabajo con una URL GET y otra PUT; consultar el resultado y comprobar el objeto de destino.

Registrar versión de imagen, formato, tamaño, páginas, nivel de análisis, concurrencia, tiempos y resultados. Compartir incidencias con referencias de trabajo y extractos de logs sin contraseñas, firmas de URLs ni documentos personales.

## Salud y observación

`/api/health` informa disponibilidad de PostgreSQL, presencia del ejecutable OCR, espacio libre en `/data`, colas y configuración de procesos. HTTP 503 indica degradación. No prueba un login real en Keycloak, una escritura en un bucket ni la calidad de una lectura OCR.

Revisar logs del contenedor, CPU/RAM, almacenamiento del PVC y disco temporal. Observar profundidad de colas y estados `failed`/`verification_failed`. Un aumento de procesos multiplica conexiones y tareas OCR; ajustar una variable a la vez y medir.

## Retención y respaldos

El mantenimiento ejecuta limpieza periódica, aproximadamente cada hora. Los temporales interactivos de más de una hora son candidatos a eliminación. Para documentos completados y otros estados previstos por el código con `protected_at`, elimina originales y archivos de trabajo al superar `RETENTION_DAYS_ORIGINAL`, y copias protegidas al superar `RETENTION_DAYS_PROTECTED`.

La retención no borra automáticamente toda la información de PostgreSQL ni los documentos sin fecha de protección. SUBTEL debe definir su política de conservación de hallazgos, usuarios, auditoría y expedientes, así como el ciclo de vida de los objetos en sus buckets.

Respaldar PostgreSQL y `/data` del mismo ambiente de forma consistente, suspendiendo escrituras si el mecanismo elegido no proporciona una instantánea coordinada. Los backups también contienen información personal. Probar una restauración en un ambiente separado antes de depender del procedimiento.

## Actualizaciones y recuperación

La [guía de versiones y actualización](VERSIONES.md) registra los cambios de preparación de esta entrega y desarrolla el procedimiento para seleccionar una revisión, conservar la configuración, comparar resultados en DEV y promover la imagen validada.

Usar una imagen identificada por versión o digest. Validar en DEV, respaldar PROD y programar una ventana de actualización. La estrategia `Recreate` del ejemplo detiene el pod anterior antes del nuevo.

La ruta interactiva recupera documentos interrumpidos a partir del estado en PostgreSQL y los archivos persistentes. Los trabajos masivos se pueden reclamar nuevamente tras vencer su reserva; el integrador debe tolerar una escritura repetida del mismo destino. Las URLs que expiren durante la interrupción requieren una nueva solicitud con firmas válidas.

Para retroceder una versión, comprobar compatibilidad con el esquema existente. Volver solo a una imagen anterior no restaura automáticamente una base ni un volumen. El alcance de restauración debe incluir ambos cuando sea necesario.

## Pruebas automatizadas

Las pruebas usan documentos ficticios y una base desechable cuyo nombre termina en `_test`. **La suite elimina el esquema `public` de esa base al empezar.** No apuntar `DATABASE_URL` a DEV o PROD.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export DATABASE_URL='CONEXION_A_BASE_DESECHABLE_TERMINADA_EN_test'
env -u MIGRATE_SQLITE_PATH -u EXPLICIT_RULES_FILE .venv/bin/python -m pytest -q
```

Instalar además Tesseract, Poppler y LibreOffice como en el Dockerfile. `tests/conftest.py` configura credenciales exclusivamente para las pruebas; no son valores predeterminados de la aplicación.

La automatización de GitHub construye la imagen Docker y ejecuta dentro de ella las pruebas con PostgreSQL, incluyendo los cinco tratamientos en los once formatos soportados. También arranca una instalación vacía para comprobar salud, login, protección de un TXT, flujo de revisión y persistencia después de un reinicio. Las pruebas OIDC y de buckets simulan los servicios externos; la validación contra los servicios institucionales se realiza en DEV.

## Carga y capacidad

`tools/gen_corpus.py` crea un corpus ficticio para pruebas de carga; `tools/stress_http.py` mide una instalación HTTP. Ejecutar únicamente contra un ambiente de pruebas, con un usuario destinado a esa actividad. El generador utiliza `reportlab`, incluido en los requisitos de desarrollo.

```bash
.venv/bin/python tools/gen_corpus.py
.venv/bin/python tools/stress_http.py --url http://localhost:8000 \
  --password "$ADMIN_PASSWORD" --docs 20 --sessions 2 --protect
```

Comenzar con documentos representativos y baja concurrencia. Medir tiempos, RAM, CPU, disco temporal y capacidad del PVC antes de ampliar la carga. La validez de los resultados protegidos debe revisarse junto con el rendimiento.
