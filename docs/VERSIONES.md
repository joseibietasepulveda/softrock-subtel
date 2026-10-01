# Entrega del 01-10-2026 y futuras actualizaciones

## Referencia de esta entrega

Aplicación **0.6.0**. Revisión de código validada: [`9587420`](https://github.com/joseibietasepulveda/softrock-subtel/commit/9587420f017a67a404d82e3891afa25974e8f969). Las modificaciones posteriores de documentación no cambian por sí mismas el código de procesamiento.

El repositorio contiene la aplicación, pruebas con ejemplos ficticios, el Dockerfile y guías de instalación y operación. Las credenciales, documentos operativos y perfiles con datos personales se administran fuera de Git y de la imagen distribuida.

## Preparación incluida

| Área | Resultado |
|---|---|
| Instalación | Imagen con Python, Tesseract español/inglés, Poppler, fuentes y LibreOffice para DOC/XLS; ejecución con usuario 10001 |
| PostgreSQL | Conexión mediante `DATABASE_URL` y creación del esquema al arrancar; Compose incorpora PostgreSQL 16 para pruebas locales |
| Usuarios iniciales | Contraseña de administrador configurada por el operador; cuenta Regular inicial opcional; conservación de contraseñas existentes al reiniciar |
| Persistencia | Separación entre PostgreSQL y documentos en `/data`; ejemplos de volúmenes y PVC |
| Kubernetes | Configuración, Deployment de una réplica, Service e Ingress de ejemplo para adaptar a DEV y PROD |
| Detección | Vocabularios generales incluidos y perfiles complementarios opcionales mediante `EXPLICIT_RULES_FILE` |
| OCR | Segunda lectura a menor escala para letras sobredimensionadas; las cajas se trasladan a las coordenadas originales para aplicar el tratamiento |
| Documentación | Arquitectura con diagramas, flujos interactivo y masivo, API, Keycloak, parámetros de instalación y operación |

En OCR, la segunda lectura se aplica cuando la mediana de altura de las palabras reconocidas supera 80 píxeles. Se conserva la imagen original y se redondean hacia fuera las cajas transformadas. Las pruebas verifican esa cobertura y que el texto de tamaño normal no provoque una segunda lectura.

## Validación registrada

La revisión indicada aprobó **190 pruebas dentro de la imagen Docker**, con PostgreSQL 16, incluidas las cinco formas de tratamiento en los once formatos soportados. Se comprobaron también el arranque con una base vacía, el ingreso, la carga, la revisión, la protección, la descarga, la auditoría y la persistencia tras reiniciar. [Resultado de CI](https://github.com/joseibietasepulveda/softrock-subtel/actions/runs/36902226491/attempts/2).

Estos son resultados de pruebas automatizadas, no una medición de exactitud sobre todo el universo de documentos. Las pruebas OIDC y de buckets de la suite simulan los servicios externos; la conectividad, permisos y configuración institucional se comprueban en DEV de SUBTEL. La capacidad debe medirse con documentos representativos y la concurrencia esperada.

## Cómo preparar una actualización de la instalación

### 1. Identificar la versión

Registrar el commit utilizado, la imagen desplegada y la configuración del ambiente. Revisar las notas de la nueva entrega, sus requisitos de base, dependencias y variables. Descargar el código del repositorio público y construir desde una revisión identificada.

```bash
git clone https://github.com/joseibietasepulveda/softrock-subtel.git
cd softrock-subtel
git checkout COMMIT_ENTREGA
docker build -t REGISTRO_SUBTEL/proteccion-datos:VERSION .
docker push REGISTRO_SUBTEL/proteccion-datos:VERSION
```

Sustituir commit, registro y versión por los valores de la entrega. Si ya se dispone de una copia local, actualizar sus referencias y seleccionar la revisión antes de construir. Conservar las adaptaciones propias de los manifiestos en la configuración administrada por SUBTEL.

### 2. Preparar datos y configuración

- Respaldar PostgreSQL y `/data` de forma consistente y comprobar el procedimiento de restauración.
- Mantener bases, credenciales, volúmenes y configuración independientes para DEV y PROD.
- Revisar las variables nuevas o modificadas y mantener los secretos fuera de la imagen. Cambiar `ADMIN_PASSWORD` no restablece la contraseña de una cuenta ya existente.
- Si se utiliza un perfil complementario, conservar su archivo privado y su montaje. Actualizar el código no debe reemplazarlo por el fixture ficticio de pruebas. Los cambios de perfil requieren reiniciar el proceso.
- No activar `MIGRATE_SQLITE_PATH` en una instalación PostgreSQL existente como paso rutinario de actualización; la importación de SQLite es una operación específica y requiere planificación propia.

### 3. Validar en DEV

Aplicar la nueva imagen y ejecutar la [validación de instalación](OPERACION.md#validación-de-instalación): acceso, formatos representativos, hallazgos, tratamiento, aprobación según rol, descarga, auditoría y reinicio. Probar también Keycloak y buckets si están habilitados.

Para comparar calidad, procesar los mismos documentos de referencia con la versión anterior y la candidata, usando idénticas opciones y perfiles. Revisar datos omitidos, selecciones excesivas, cobertura de recuadros, legibilidad de sustitutos y tiempos. Las reglas específicas opcionales afectan a la comparación y deben registrarse.

La suite automatizada utiliza una base desechable cuyo nombre termina en `_test` y elimina su esquema `public`. No ejecutarla contra la base de la instalación que se está actualizando.

### 4. Promover y conservar una opción de recuperación

Promover a PROD la misma imagen validada en DEV y cambiar únicamente la configuración del ambiente. Dejar terminar los trabajos activos y aplicar la actualización durante una ventana prevista: el Deployment de ejemplo utiliza `Recreate` y produce una interrupción temporal.

Comprobar salud, ingreso y descarga después de actualizar. Mantener la referencia de la imagen anterior y sus respaldos. Si el esquema cambió de forma incompatible, volver a una imagen anterior puede requerir restaurar conjuntamente base y documentos; no asumir que basta con cambiar la etiqueta del contenedor.

## Registro de una actualización

Registrar fecha, responsable, commit, versión o digest de imagen, ambiente, cambios de configuración, respaldo utilizado y resultados de las pruebas. Identificar qué integraciones se comprobaron con servicios reales. No incluir contraseñas, tokens, firmas de URL ni contenido personal de documentos en ese registro.

Referencias: [arquitectura](ARQUITECTURA.md), [despliegue](../deploy/README.md), [configuración](CONFIGURACION.md), [operación](OPERACION.md) y [reglas complementarias](REGLAS_DETECCION.md).
