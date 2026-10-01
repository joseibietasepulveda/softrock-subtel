# Despliegue en Kubernetes para SUBTEL

## Responsabilidades y ambientes

SUBTEL construye y almacena la imagen en su registro, configura la red y los secretos, administra PostgreSQL y proporciona almacenamiento persistente. La aplicación puede instalarse sin acceso del proveedor a la base ni a sus credenciales.

DEV y PROD deben tener bases, credenciales y volúmenes independientes. Pueden utilizar el mismo clúster y namespaces distintos, conforme a las políticas de SUBTEL. La separación de bases no exige por sí sola un pod PostgreSQL por ambiente: el servicio de bases puede ser administrado o externo al clúster.

Los manifiestos de esta carpeta describen un punto de partida de una réplica. Sus recursos y capacidad de disco son ejemplos ajustables, no mediciones de capacidad.

## 1. Construir y registrar la imagen

Desde la raíz del repositorio:

```bash
docker build -t REGISTRO_SUBTEL/proteccion-datos:VERSION .
docker push REGISTRO_SUBTEL/proteccion-datos:VERSION
```

Sustituir registro y versión con valores de SUBTEL. La construcción necesita acceso a las fuentes de paquetes Debian y Python, o a sus espejos internos. La ejecución necesita conexión solo a PostgreSQL y a los servicios institucionales habilitados. Construir para la arquitectura de CPU de los nodos de destino.

El Dockerfile incorpora Tesseract, Poppler y LibreOffice. La aplicación corre como UID/GID 10001, escucha en 8000 y escribe documentos en `/data`; las conversiones también utilizan `/tmp` y el directorio del usuario. Al usar políticas de filesystem de solo lectura, proporcionar volúmenes escribibles para esas rutas.

## 2. Preparar PostgreSQL y secretos

Crear la base y la cuenta del ambiente con los permisos descritos en [Configuración](../docs/CONFIGURACION.md). Conservar las credenciales en un archivo privado fuera del repositorio o en el gestor institucional. El archivo de variables debe incluir:

```dotenv
DATABASE_URL=CONEXION_POSTGRESQL_DEL_AMBIENTE
ADMIN_PASSWORD=CONTRASEÑA_INICIAL_ELEGIDA_POR_SUBTEL
```

La contraseña inicial debe cumplir la política indicada en el README. Para Keycloak agregar `OIDC_CLIENT_SECRET` si el cliente lo requiere. El archivo no es una plantilla para publicar ni debe incorporarse a una imagen.

Ejemplo de creación de Secret, dentro de un namespace previamente provisionado:

```bash
kubectl -n subtel-dev create secret generic subtel-secrets \
  --from-env-file=/ruta/privada/subtel-dev.env
```

En PROD utilizar otro namespace, archivo, base y valores. Configurar `imagePullSecrets` en el Deployment si el registro lo requiere.

## 3. Adaptar los manifiestos

| Archivo | Adaptaciones |
|---|---|
| [configmap.yaml](kubernetes/configmap.yaml) | OCR, concurrencia, límites, retención, hosts de buckets y variables OIDC |
| [pvc.yaml](kubernetes/pvc.yaml) | Capacidad y clase de almacenamiento del clúster |
| [deployment.yaml](kubernetes/deployment.yaml) | Imagen, recursos y políticas de seguridad compatibles con el clúster |
| [service.yaml](kubernetes/service.yaml) | Servicio interno en puerto 80 hacia el contenedor en 8000 |
| [ingress.yaml.example](kubernetes/ingress.yaml.example) | Dominio, clase y certificado; adaptar al gateway utilizado |

El PVC de ejemplo solicita 50 GiB con `ReadWriteOnce`. El Deployment usa una réplica y estrategia `Recreate` para evitar dos pods simultáneos usando el mismo almacenamiento. Una actualización interrumpe temporalmente el servicio. UID/GID 10001 debe poder escribir en el volumen; `fsGroup` requiere soporte del almacenamiento correspondiente.

Los valores de ejemplo son requests de 0,5 CPU/1 GiB RAM y límites de 2 CPU/4 GiB RAM. Ajustarlos según tamaños de documentos y concurrencia medidos en DEV. Configurar también cuotas de almacenamiento efímero acordes con el procesamiento masivo.

Para Keycloak agregar al ConfigMap `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_REDIRECT_URI` y `OIDC_ROLE_MAP`. Mantener el secreto del cliente en el Secret. [Ver integración](../docs/INTEGRACIONES.md#keycloak-y-cuentas-institucionales).

## 4. Aplicar y comprobar

```bash
kubectl -n subtel-dev apply -f deploy/kubernetes/configmap.yaml
kubectl -n subtel-dev apply -f deploy/kubernetes/pvc.yaml
kubectl -n subtel-dev apply -f deploy/kubernetes/deployment.yaml
kubectl -n subtel-dev apply -f deploy/kubernetes/service.yaml
kubectl -n subtel-dev rollout status deployment/subtel
kubectl -n subtel-dev port-forward service/subtel 8000:80
```

Desde otro terminal, consultar `http://localhost:8000/api/health` y abrir la interfaz. El Ingress es un ejemplo y no se aplica automáticamente: adaptar el archivo, guardarlo con extensión `.yaml` y aplicarlo con el namespace correcto.

`startupProbe` y `readinessProbe` consultan la salud de base, OCR y disco. `livenessProbe` comprueba el puerto para no reiniciar continuamente el contenedor ante una caída externa de PostgreSQL. Los tiempos deben ajustarse a la instalación.

## 5. Gateway, red y autenticación

Publicar la aplicación en la raíz de su dominio, con HTTPS. Configurar el tamaño máximo de solicitudes y los tiempos de espera del gateway según los límites de carga. Las cargas y los trabajos masivos son asíncronos; la ruta de protección directa mantiene la solicitud abierta hasta terminar.

Permitir comunicación desde el pod hacia PostgreSQL, Keycloak y los hosts de bucket configurados. Permitir DNS. Las políticas de red y certificados internos son propias de SUBTEL; estos ejemplos no las sustituyen. Si se usa una CA interna, incorporarla al almacén de confianza correspondiente sin desactivar la validación TLS.

Configurar en Keycloak la misma URL HTTPS indicada por `OIDC_REDIRECT_URI`. La autenticación Google se resuelve en Keycloak. El navegador necesita acceso al gateway y a Keycloak; no necesita acceso directo a PostgreSQL.

## 6. Promover y actualizar

Validar el recorrido de carga, revisión, protección, aprobación y descarga en DEV; probar además Keycloak y buckets cuando se habiliten. Promover a PROD la misma imagen identificada, cambiando solo la configuración del ambiente.

Antes de actualizar, respaldar PostgreSQL y `/data` de manera consistente. Para reducir tareas interrumpidas, dejar terminar los trabajos activos y suspender nuevas cargas durante la ventana de actualización. [Operación y recuperación](../docs/OPERACION.md).
