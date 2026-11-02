# Informe de cambios — revisión de extensión, 17/oct/2026

## Inventario del material recibido

Se recibió solamente una extensión Chromium MV3 con 11 entradas: `background.js`, `lib.js`, `popup.js/html/css`, `manifest.json`, `config.js` e iconos. No había `autofill.js`, content scripts, pruebas, frontend Astro, backend, módulo IA ni bases de datos.

Observaciones antes de cambios:

1. **Bloqueante:** ningún motor rellena formularios. El popup se limita a consultar, copiar y descargar documentos.
2. **Privacidad:** `config.js` tenía identificación personal y código de vinculación expirado. Se sustituyó por configuración pública sin código ni correo personal.
3. **Privacidad:** la versión anterior persistía el contenido completo de documentos en `chrome.storage.local`; ahora se guarda en `chrome.storage.session`, se migra el estado anterior y se elimina esa copia local.
4. **Integración:** `GET /api/extension/autofill` aparece en la descripción del TT, pero en el ZIP no hay backend que compruebe su existencia o respuesta. Requiere integración.

## Archivos cambiados/añadidos

- `autofill-core.js`: clasificación semántica, normalización, mapeo de datos y filtros negativos; nunca inventa valores.
- `autofill.js`: escritura en formularios, eventos compatibles con frameworks y observación DOM de campos tardíos; sin envío.
- `background.js`: autorización de origen, inserción en pestaña activa, modo Siempre aquí y control de mensajes.
- `lib.js`: consulta de perfil validado, saneamiento URL, sesión de documentos, revocación y limpieza.
- `popup.js` y `popup.css`: controles de autocompletado, selección explícita de documento, apariencia indigo/cian refinada.
- `manifest.json`: scripting, activeTab, tabs y permisos de host opcionales; versión 1.1.0.
- `config.js`: eliminación de datos personalizados y código vencido.
- `README.md`, `TEST_AUTOFILL.html`, `docs/INTEGRACION_Y_AZURE.md`, `tests/`: documentación y comprobaciones.

## Algoritmos realmente implementados en la extensión

| Algoritmo | Uso | Restricción |
|---|---|---|
| Normalización léxica NFD + camelCase + separación de tokens | Comparar `apellidoPaterno`, `apellido-paterno`, `Apellido paterno` | No recupera datos no presentes |
| Priorización determinista de señales | `autocomplete` (100) > `label` (90) > `aria` (85) > `data-*` (75) > `name`/`id` > `placeholder` | Algunos formularios ambiguos quedan sin rellenar |
| Regex de inclusión y exclusión | Clases de nombre, CURP, CP, fechas, domicilio y bloqueo de campos sensibles | No utiliza LLM para formularios web |
| Lista de campos admitidos | Solo se procesan claves confirmadas, se omite lo desconocido | Falsos negativos preferibles a exponer otro dato |
| Mutaciones del DOM + eventos input/change | Detectar componentes dinámicos y formularios reactivos | Observador con límite de tiempo y cambios |
| Control del origen exacto | Restringir autocompletado persistente a dominios autorizados | El origen autorizado puede leer el valor rellenado |

## Pruebas realizadas

- `node --test tests/core.test.cjs`: **27/27 aprobadas** (inicialmente detectó y se corrigieron 3 casos).
- `python tests/dom_e2e.py`: **aprobado** en Chromium usando Playwright, con 9 campos sintéticos completados, exclusión de campos sensibles, preservación de preexistentes, disparo de evento input, formulario dinámico, cero envíos.
- `node --check`: sintaxis de JavaScript verificada.
- Extensión MV3 cargada como paquete completo: **pendiente**, el Chromium de este entorno tiene la carga de extensiones desempaquetadas **bloqueada por una política del administrador** (mensaje real: `Loading of unpacked extensions is disabled by the administrator`). Esto no demuestra un error del manifiesto; queda pendiente la prueba manual en Chrome/Edge sin esa política.
- API real, vinculación, INE, OCR, PostgreSQL/MongoDB, registro y Azure: **no probados** al no recibir el repositorio central y su entorno.

## Pendientes prioritarios

1. Incorporar el código al `extension/` del `tt-main` más reciente y repetir las pruebas contra la implementación real.
2. Implementar o contrastar la ruta segura `/api/extension/autofill` a partir de un perfil confirmado del mismo usuario.
3. Probar manualmente `activeTab`, concesión de permisos, `Siempre aquí`, Edge y Chrome reales.
4. Auditar y corregir flujo de alta de usuario, obligación de INE y pantalla de revisión dentro de Astro/Express.
5. Medir OCR/LLM frente a muestras autorizadas antes de modificar modelo o afirmar eficacia.
6. Compilar, contenerizar y desplegar `tt-main` en Azure, con recursos y secretos correctamente configurados.
