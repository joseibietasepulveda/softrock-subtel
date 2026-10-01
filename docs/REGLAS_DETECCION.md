# Reglas complementarias de detección

El motor incluye un vocabulario general de nombres, expresiones regulares, reglas de contexto y OCR. Opcionalmente puede cargar un archivo JSON con coincidencias literales y correcciones específicas mediante `EXPLICIT_RULES_FILE`.

Los perfiles que contienen nombres y fragmentos de documentos reales se entregan por un canal privado y se mantienen fuera de Git. Una instalación sin ese perfil conserva la detección general, pero puede detectar menos datos en los casos específicos para los cuales se crearon las reglas. Para reproducir esas detecciones se debe montar el mismo perfil privado.

Estas reglas mejoran secuencias concretas; no enseñan automáticamente a reconocer cualquier nombre nuevo. La calidad y el tiempo de OCR también dependen del documento, los idiomas instalados y el nivel de análisis.

## Estructura del perfil

| Campo | Función |
|---|---|
| `names` | Nombres y secuencias literales complementarias |
| `reference_sequences` | Secuencias normalizadas que habilitan lecturas complementarias en un documento |
| `header_tokens` | Secuencia esperada que se busca en un encabezado no leído |
| `paragraph_rules` | Secuencias permitidas, categoría interna y correcciones OCR verificadas |
| `completion_groups` | Grupos de resultados que permiten terminar una relectura |

Los tokens de comparación se escriben en minúsculas, sin acentos, según la normalización del motor. Los nombres de `names` mantienen su escritura legible. Las coincidencias de una palabra exigen mayúsculas y acentos exactos para reducir falsos positivos. El archivo contiene datos JSON, no código ejecutable.

El [perfil de prueba](../tests/fixtures/explicit_rules.json) contiene datos ficticios y muestra la estructura. No se carga por defecto ni sustituye el perfil operativo privado.

## Carga en Kubernetes

SUBTEL recibe el archivo por un canal privado. Crear un Secret desde una ruta fuera del repositorio:

```bash
kubectl -n subtel-dev create secret generic subtel-reglas \
  --from-file=rules.json=/ruta/privada/reglas-deteccion.json
```

Agregar un volumen al Deployment:

```yaml
volumes:
  - name: reglas
    secret:
      secretName: subtel-reglas
```

Y el montaje al contenedor, conservando los volúmenes ya existentes:

```yaml
volumeMounts:
  - name: reglas
    mountPath: /etc/subtel-reglas
    readOnly: true
```

Definir `EXPLICIT_RULES_FILE=/etc/subtel-reglas/rules.json` en el ConfigMap y reiniciar la aplicación. Un archivo inexistente o JSON inválido provoca un error de arranque; no se ignora silenciosamente. El perfil se carga una vez por proceso, de modo que los cambios requieren reinicio.

Para ampliar reglas, mantener el perfil completo y agregar las nuevas secuencias. Comprobar con documentos de referencia que se protege el dato completo sin alterar texto vecino. La misma configuración conserva las reglas específicas; medir por separado la calidad y rendimiento del OCR en DEV.
