# Integración TT — revisión del repositorio `tt-main` (18/10/2026)

## Alcance real

Código existente revisado: Astro frontend, Node/Express, módulo Flask/Docling/Tesseract/Qwen, PostgreSQL, MongoDB, extensión MV3, Nginx y Compose. La revisión se basó en `tt-main.zip` de esta conversación y el paquete mejorado de extensión. No se han usado documentos personales reales como corpus de pruebas.

## Cambios aplicados

| Parte | Implementación | Archivos principales |
|---|---|---|
| Registro obligatorio con INE | Ahora la aceptación de términos solo habilita la carga de INE; la sesión de aplicación completa se emite únicamente tras una INE `validado`, perfil confirmado y consentimiento registrado. Los endpoints de registro emplean sesión limitada y comprueban pertenencia de documentos. | `backend/src/routes/auth.ts`, `backend/src/middleware/auth.ts`, `backend/src/db/schema.sql` |
| Consentimiento | Registro versionado de términos, aviso y consentimiento expreso con fecha, IP y agente de usuario. Requiere tres selecciones independientes en la interfaz. | `backend/src/db/schema.sql`, `frontend/src/pages/registro.astro` |
| Revisión humana | Presentación editable de campos extraídos de la INE y confirmación expresa. Reutiliza el mismo componente de validación del proyecto para no duplicar lógica. | `frontend/src/lib/validacion.ts`, `frontend/src/pages/registro.astro` |
| Seguridad perimetral | La ruta de subida de INE tiene límite de 12 MB en Nginx, no los 16 KB heredados de autenticación. Las consultas posteriores usan límite menor y su propio rate limit. | `nginx/templates/default.conf.template` |
| Extensión | Integrados los archivos de extensión MV3 del paquete mejorado: reglas de autocompletado, formularios dinámicos, sitio autorizado y pruebas sintéticas. | `extension/autofill-core.js`, `autofill.js`, `background.js`, `popup.js`, `tests/` |
| Endpoint autofill | Perfil obtenido desde PostgreSQL solo si `registro_completo` y `perfil_usuario.verificado`; descifra CURP y domicilio en servidor y nunca devuelve credenciales de usuario ni documentos OCR sin confirmar. | `backend/src/routes/extension.ts`, `backend/src/services/extension.ts` |
| Auditoría OCR | Las listas de campos descartados por anti-alucinación ya no incluyen los valores personales en metadatos ni logs, solo nombres de campo. | `ia_documentos/application/pipeline.py` |
| Azure | Overlay Compose para VM Ubuntu en Azure y guía explícita de NSG, certificado TLS, secretos, modelos, backups y comprobaciones. | `deploy/azure/compose.vm.yml`, `deploy/azure/README.md` |
| Preparación local | Creado `.env.example` SIN secretos; compatibles los scripts de arranque existentes. | `.env.example` |

## Algoritmos del proyecto y mejoras

1. **Extracción documental híbrida:** Docling y/o Tesseract (preprocesamiento y zonas), clasificadores por texto documental, reglas/regex, Qwen mediante Ollama, fusión, verificación anti-alucinación y esquemas Pydantic. Ya existía en el repositorio. Se preservó por falta de corpus de INE etiquetado; NO se puede afirmar mejora cuantitativa de precisión OCR sin correr comparaciones de campo sobre documentos autorizados.
2. **Autocompletado:** normalización Unicode, búsqueda semántica ponderada de `autocomplete`, `label`, `aria-label`, `name`, `id`, `placeholder`; expresiones regulares con palabras negativas para impedir correspondencias inseguras; actualización mediante setters nativos y eventos `input`/`change`; `MutationObserver` para campos dinámicos. Nunca rellena contraseñas, OTP ni tarjetas ni envía el formulario.
3. **Registro:** máquina de estados `cuenta → correo → consentimiento → INE → revisión → sesión completa`, con **guarda autoritativa en Express/PostgreSQL**. Las cookies tienen ámbitos separados (`registro` vs `sesion`).
4. **Persistencia:** PostgreSQL para relaciones, MongoDB para resultado flexible cifrado de OCR; almacenamiento cifrado AES-GCM. El sistema conserva auditoría valor OCR vs valor confirmado.

## Pruebas ejecutadas en este entorno

- `node --test extension/tests/core.test.cjs`: **27 aprobadas, 0 fallidas** (clasificador y reglas de autocompletado).
- `node` usando parser TypeScript en 54 fuentes / scripts Astro: **54 analizados, 0 errores de sintaxis**. Esto NO equivale a `tsc` ni a un build de Astro.
- `python -m compileall ia_documentos`: **sin errores de sintaxis**.
- `node --check` de `extension/background.js` y `extension/autofill.js`: **sin errores de sintaxis**.
- `tsc -p backend/tsconfig.json --noEmit`: **bloqueado** por dependencias NPM incompletas; falla con `TS2688 Cannot find type definition file`, NO es compilación aprobada.
- `npm ci --prefix backend` no finalizó en el entorno de ejecución. No se pudo reconstruir el frontend ni ejecutar los tests E2E reales con Flask/PostgreSQL/MongoDB/Ollama.
- No hubo acceso a un navegador real con extensión cargada, al corpus de INEs etiquetadas ni a una suscripción de Azure. **El sistema no ha sido validado de extremo a extremo ni desplegado**.

## Pruebas de aceptación obligatorias ANTES de presentar como listo

1. Construcción completa: `npm ci --prefix backend && npm run build --prefix backend`, `npm ci --prefix frontend && npm run build --prefix frontend`.
2. Registro: correo verificado, términos rechazados, consentimiento faltante, ausencia de INE, INE rechazada, validación correcta, usuario ajeno, doble confirmación, intento de endpoint directo antes de finalizar y persistencia tras reinicio.
3. OCR: al menos 30–50 muestras autorizadas por tipo, con métricas por campo (nombre, CURP, domicilio, fechas, clave de elector), falsos rechazos, tiempos y tasas de revisión humana. No inventar porcentaje de precisión.
4. Extensión: cargarla en Chrome/Edge, vincular, `given-name` / `family-name` / CURP / domicilio / campos dinámicos / formularios React, revocar token, orígenes HTTPS, confirmar que no se autoinvoca en páginas no autorizadas.
5. MongoDB y PostgreSQL reales, restauración de backups, auditoría, cifrado, alta concurrente y errores de desconexión.
6. Docker Compose y Azure: certificados CA válidos, resolución DNS, NSG, servicios internos cerrados, correo real, monitorización y restauración.

## Limitaciones y protección de datos

- **OCR no autentica la INE:** la persona indica que es titular y confirma los datos, pero no hay contraste con un servicio oficial del INE. No prometer KYC o prevención de fraude.
- La solución cubre campos estándar y heurísticas frecuentes, **no todos los sitios externos**. CAPTCHA, campos inaccesibles, shadow DOM cerrado e iframes de terceros pueden necesitar trabajo adicional.
- Sin el Word oficial del TT no se pueden atribuir estados a RF/RNF con identificadores originales.
- No incluir en Git ni en la distribución el `.env` original, volúmenes, respaldos, documentos de identidad ni `nginx/certs/key.pem`. La versión ZIP generada omite todos estos datos. **Conserva las claves previas si quieres volver a abrir los datos cifrados antiguos**; no crees una clave nueva sobre bases previas.
- Texto de aviso y consentimiento requiere revisión legal en México antes de admitir datos reales en producción.

## Arranque local / Azure

Para Windows con WSL/Docker funcionando: ejecutar `EJECUTAR_TT.bat`. Si ya tienes datos, copia solo código actualizado sobre una copia de seguridad del proyecto actual y conserva los volúmenes, `.env` y claves que ya utilizas. Para Azure VM: `deploy/azure/README.md`. Para servicio administrado se requiere adaptar almacenamiento, cómputo IA y bases de datos; aún no configurado.
