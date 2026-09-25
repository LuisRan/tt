# Extensión de navegador (Chrome / Edge / Brave / Opera, Manifest V3)

Bóveda de bolsillo: muestra los documentos **ya validados** del usuario para consultar y copiar
sus datos sin entrar a la plataforma. Esta carpeta es la plantilla: el backend la empaqueta
**personalizada por usuario** (`POST /api/extension/paquete`) y la entrega como
`ocr-platform-extension.zip`.

## Qué hace

| Pantalla | Contenido |
|---|---|
| Inicio | Botón **Sincronizar**, fecha de la última sincronización y las 4 categorías (INE, CURP, acta de nacimiento, pasaporte) con su conteo. |
| Categoría | Documentos con su identificador `0001_CSC` (folio de 4 dígitos = orden en que se confirmó + iniciales del titular), titular, fecha, *vigente*/*versión anterior*/*vencido*. |
| Documento | Cada dato con botón **copiar**, **Copiar todo**, **Descargar .txt** y, plegado, el texto escaneado (OCR). |
| Vincular | Si no está vinculada: código `XXXX-XXXX` y dirección de la plataforma. |

## Archivos

| Archivo | Función |
|---|---|
| `manifest.json` | MV3. Incluye `key` (clave pública) para que el ID sea siempre `pllafflpedhmndcofabmgpcminahiidj`; el backend reescribe `host_permissions` y `externally_connectable` con `PUBLIC_URL`. |
| `config.js` | En el paquete lo genera el backend: `{ url, codigo, expira_en }`. El de esta carpeta es solo para desarrollo. |
| `lib.js` | Núcleo compartido: almacenamiento (`chrome.storage.local`), vincular, sincronizar, desvincular. |
| `background.js` | Service worker: se vincula sola al instalarse (código de `config.js`) y atiende mensajes de la página `/app/extension` (estado, vincular, sincronizar) solo desde el origen de la plataforma. |
| `popup.html/.css/.js` | Interfaz. Todo se pinta con `textContent` (sin `innerHTML` con datos). |
| `icons/` | Íconos 16/32/48/128. |

## Seguridad

- La extensión **nunca** recibe la contraseña. Se vincula con un código de un solo uso (15 min,
  guardado como HMAC) que se canjea por un **token de dispositivo** (Bearer, guardado como HMAC).
- El token solo permite **leer** documentos validados (`GET /api/extension/documentos`) y desvincularse.
- Se revoca desde la plataforma (Extensión > Extensiones vinculadas), al cambiar la contraseña, al
  "cerrar todas las sesiones" o si el administrador deshabilita la cuenta. En la siguiente
  sincronización la extensión recibe 401 y **borra los datos** que guardaba.
- Los datos quedan en el perfil del navegador (`chrome.storage.local`); "Desvincular" los elimina.

## Instalación manual (desarrollo)

1. `chrome://extensions` → **Modo de desarrollador** → **Cargar descomprimida** → esta carpeta.
2. Abre la extensión y escribe un código generado en la plataforma (Extensión > Generar código).

Chrome no permite instalar extensiones fuera de la Chrome Web Store sin el modo de desarrollador;
para distribuirla a usuarios finales habría que publicarla en la tienda (o por políticas de empresa).
