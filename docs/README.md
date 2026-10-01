# Documentación técnica para SUBTEL

La documentación describe el código entregado y las responsabilidades de instalación y operación.

1. [Arquitectura](ARQUITECTURA.md): componentes, dos recorridos de documentos, datos persistidos y escalamiento.
2. [Despliegue](../deploy/README.md): imagen Docker, manifiestos Kubernetes, gateway y separación DEV/PROD.
3. [Configuración](CONFIGURACION.md): variables, PostgreSQL y parámetros de capacidad.
4. [Integraciones](INTEGRACIONES.md): contratos de API, buckets, Keycloak y roles.
5. [Operación](OPERACION.md): validación, respaldos, monitoreo, actualización y pruebas.
6. [Reglas de detección](REGLAS_DETECCION.md): perfiles privados y conservación de reglas específicas.
7. [Versiones y actualización](VERSIONES.md): preparación de la entrega del 01-10-2026, validación registrada y procedimiento para instalar futuras versiones.

Los ejemplos usan dominios reservados y campos vacíos para credenciales. Se deben completar con los valores administrados por SUBTEL. La [referencia de archivos](ARQUITECTURA.md#componentes-y-código) permite relacionar cada componente con su implementación.
