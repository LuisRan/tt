# Autocompletado v1.2.0 — parche v07

Corrige el nombre de pila que quedaba vacío al seleccionar una INE y amplía el reconocimiento de formularios. El contrato real de la API entrega `campo: nombres` junto a la etiqueta `Nombre(s)`; la versión anterior ignoraba `campo` y convertía ese dato en nombre completo.

## Cambios

- Prioridad a las claves estructuradas de datos confirmados; no dividir nombres completos por suposiciones ni mezclar el perfil del dueño con documentos de otra persona.
- Etiquetas HTML, varios IDs en `aria-labelledby`, texto cercano al control, atributos `name`/`id`/`data-*` y tokens `autocomplete` con secciones. Ante señales fuertes contradictorias, el campo queda pendiente.
- Día, mes y año a partir de una fecha de nacimiento confirmada, con calendario real. Fechas de texto con formato explícito y controles `date`/`number`.
- Meses en español/inglés, opciones numéricas, valores internos opacos y meses indexados desde cero cuando sus etiquetas lo permiten. Placeholders como `Seleccionar el día` con valor `0` ya no impiden el relleno.
- Sexo: el contrato de la API mexicana usa **H = hombre, M = mujer**. Las etiquetas de listas y radios permiten adaptar esto a controles que utilizan otros códigos, incluido M/F. Una letra M aislada y ambigua no se selecciona.
- Separación entre estado de nacimiento y estado del domicilio, con equivalencias de nombres/códigos de entidades. Los códigos electorales numéricos de una INE no se usan como estado de nacimiento o domicilio.
- Formularios dinámicos y Shadow DOM abierto; setters nativos, eventos `input`/`change`, comprobación del valor realmente aceptado y contador actualizado mientras el popup permanece abierto.
- Conservación de valores existentes y ediciones del usuario. Respeto a controles ocultos, deshabilitados, readonly, límites y patrones. No rellenar credenciales, OTP, CAPTCHA, datos bancarios ni datos de padres o contactos de emergencia.
- Revalidación del documento seleccionado mediante la API antes de rellenar. Mensaje dirigido al documento principal concreto; cambios de URL, desvinculación, cambio de fuente y retirada de permisos cancelan las pasadas pendientes.

No se envían formularios, HTML ni perfiles a modelos o servicios externos. El motor se ejecuta localmente. Se conservan la clave pública que define el ID de la extensión y sus permisos. Las pasadas dinámicas duran como máximo 18 segundos, con límites de controles, raíces y ejecuciones.

## Compatibilidad y aplicación

Base disponible: `tt-integracion.zip`, commit `5ece3ab`, más el parche OCR v06 recuperado. Ese parche de OCR no cambia archivos de la extensión. La referencia de continuidad identifica tu rama como `feature/ocr-mejorado-v06`, commit `7b84062`; no se ha consultado su estado remoto actual. La comprobación de aplicación sobre tu checkout es obligatoria antes de modificarlo.

En tu repositorio local, con los cambios anteriores guardados:

```powershell
git switch feature/ocr-mejorado-v06
git status --short
# Continúa cuando el árbol esté limpio; conserva cualquier cambio pendiente.
git switch -c feature/extension-autofill-v07

git apply --check "$HOME\Downloads\tt-extension-autofill-v07.patch"
git apply "$HOME\Downloads\tt-extension-autofill-v07.patch"
git diff --check
```

Si la comprobación informa conflictos, no fuerces el parche ni reemplaces archivos completos. Hay que adaptarlo al contenido actual. El parche no modifica configuración de Docker/Nginx, variables de entorno, secretos, volúmenes, respaldos ni procesamiento OCR.

## Verificación reproducible

Pruebas sin dependencias adicionales, desde la raíz del proyecto:

```powershell
node --test extension/tests/core.test.cjs tests/extension/core-regression.test.cjs tests/extension/worker.test.cjs
```

Para añadir las pruebas de navegador (Node.js 20 o posterior):

```powershell
npm ci --prefix tests/extension
node tests/extension/node_modules/playwright/cli.js install chromium
npm test --prefix tests/extension
```

Puede indicarse un Chromium ya instalado mediante `CHROMIUM_EXECUTABLE`. Las dependencias de prueba quedan fuera de `extension/`, por lo que no se incluyen en el ZIP de la extensión generado por la plataforma.

Resultado durante la preparación: **124 pruebas aprobadas**, incluidas **17 integraciones en Chromium 143**, con Node.js 24 y Playwright 1.62.1. Se reprodujo el fallo de nombres tipados contra el motor anterior. Se comprobó la aplicación del parche sobre una copia de la base disponible con OCR v06 y la ausencia de errores de espacio en el diff.

Las páginas de prueba son sintéticas y las solicitudes de red se interceptan. No contienen datos de identificaciones reales. No se probó el formulario oficial en vivo: el portal de CURP devolvió acceso denegado desde el navegador de inspección. La reproducción utiliza los controles visibles en la captura; no demuestra compatibilidad con todo el HTML actual del portal ni con todos los sitios.

## Activar y comprobar la actualización

1. Lleva los seis archivos modificados de `extension/` a la copia del proyecto que sirve la plataforma, mediante tu flujo habitual de Git/SSH.
2. Si usas la descarga de la plataforma, descarga nuevamente la extensión y extrae su contenido. No publiques el `config.js` personalizado de una descarga: contiene un código temporal de vinculación.
3. Recarga la extensión en `chrome://extensions` o `edge://extensions`, conservando su instalación/ID. Recarga también la página del formulario para retirar el script antiguo.
4. Verifica que la versión sea **1.2.0**, sincroniza documentos y prueba el relleno manual. Revisa los valores antes de enviar.

No se puede completar un dato que no existe entre los valores confirmados. En particular, la INE normalmente no entrega un estado de nacimiento como campo separado: si no está disponible, el popup lo informa y lo deja pendiente. No se deducen fecha, sexo o entidad a partir de CURP, ni se convierte el domicilio en lugar de nacimiento. Los widgets personalizados que no son controles HTML nativos, iframes y Shadow DOM cerrado requieren adaptación específica; no se amplían permisos para acceder a ellos.
