# Integración pendiente: registro INE + OCR + perfil + Azure

> Documento de diseño para integrar esta extensión en `tt-main`. No es una afirmación de despliegue realizado ni de cumplimiento completo del TT. No se recibió el frontend/backend original en este ZIP.

## Arquitectura de tres capas del TT

```text
1. PRESENTACIÓN (navegador)
   Astro 5 (registro, revisión INE, dashboards) + Extensión Chrome/Edge Manifest V3
                  | HTTPS + API REST
2. LÓGICA / SERVICIOS
   Express 5 + TypeScript (identidad, reglas, autorización, validación Zod)
                  | cola de trabajos privada
   Python + Flask (OpenCV -> OCR/Docling -> extracción Qwen -> Pydantic)
                  | conexiones privadas
3. DATOS
   PostgreSQL 16 (usuarios, perfil, consentimientos, validaciones, versiones)
   MongoDB 7 (extracción semiestructurada y evidencias de corrección)
   Almacenamiento cifrado (documentos originales y respaldos)
```

Nginx es reverse proxy, no una cuarta capa de negocio. Python/LLM son servicios de aplicación, no una capa independiente. Las tres capas de seguridad perimetral, de aplicación y de datos son una clasificación distinta.

## Registro obligatorio con INE: contrato y flujo propuesto

Estado mínimo de cuenta: `pendiente_correo` → `pendiente_consentimiento` → `pendiente_ine` → `procesando_ine` → `revision_ine` → `registro_completo`.

1. Crear usuario con correo y contraseña; verificar correo. Su sesión inicial está limitada por el backend.
2. Presentar términos, aviso de privacidad y consentimiento expreso **antes** de subir la INE; conservar versiones y fecha, identificador de sesión y trazas mínimas necesarias.
3. Permitir cargar ambas caras en JPG/PNG/PDF o fotografía desde cámara con consentimiento. En móvil `capture="environment"` puede ofrecer cámara; en escritorio `getUserMedia()` necesita permisos y HTTPS. Validar tamaño, contenido real, orientación y que haya reverso.
4. Clasificar INE, extraer campos con OCR y reglas, entregar cada campo con estado `extraido`, `faltante` o `requiere_revision`, sin transformar automáticamente un dato poco fiable en definitivo. Permitir reintentar/cambiar imágenes.
5. Mostrar página **Revisar los datos de tu INE** con nombre(s), apellidos, CURP si aparece, sexo, fecha de nacimiento, domicilio y otros campos definidos en el TT; entradas editables y marcadores de campos sin detectar. Vista clara de origen del valor y diferencia entre detección y confirmación.
6. Tras una acción explícita de usuario, guardar correcciones auditables como una versión de datos confirmados. **No** asumir que reconocer texto en una INE demuestra autenticidad del documento ni identidad del propietario.
7. `POST /api/auth/registro/finalizar` debe verificar de nuevo en servidor: correo verificado, consentimiento activo, documento tipo INE vinculado al usuario, procesamiento correcto y validación explícita de sus datos. Actualizar `registro_completo` dentro de una transacción adecuada.
8. El middleware de autorización deniega rutas normales a cualquier sesión incompleta, incluso si manipulan las peticiones HTTP o el frontend.

**No se pudo implementar/probar lo anterior:** faltan `frontend/`, `backend/`, `ia_documentos/`, `schema.sql`, contratos reales y ejemplos de INE autorizados.

## Contrato mínimo de API para el autocompletado

`GET /api/extension/autofill`

- Requiere token de **dispositivo de extensión**, revocable, distinto del JWT de sesión.
- Verifica el propietario, `registro_completo=TRUE`, consentimiento vigente, perfil validado y permisos.
- Responde solo con campos permitidos y confirmados, nunca texto OCR crudo, foto de INE, claves de acceso o datos de otros titulares.
- Deshabilitar caché (`Cache-Control: no-store`); registrar auditoría sin almacenar valores privados.
- Rechaza con `401` tokens revocados o expirados; `403` perfiles sin permiso; `409` perfil aún sin confirmar.

Respuesta **ilustrativa y sintética** (los campos y validaciones concretos deberán cotejarse con la API real):

```json
{
  "validado": true,
  "perfil": {
    "nombre": "ALBA",
    "apellido_paterno": "EJEMPLO",
    "apellido_materno": "PRUEBA",
    "correo": "demo@example.invalid",
    "curp": "EJPA000101MDFRRL01",
    "fecha_nacimiento": "2000-01-01",
    "codigo_postal": "01234",
    "domicilio_completo": "CALLE FICTICIA 12"
  }
}
```

Esta API no debe usar como perfil todos los documentos del usuario indiscriminadamente: algunos pueden corresponder a otros titulares. La fuente de datos ha de ser `perfil_usuario` confirmado y autorizado. Cuando un valor no se tiene con certeza, omitirlo o devolver `null`.

## Algoritmos OCR/IA para evaluar (no implementados en esta entrega)

- Clasificación por señales documentales y MRZ `IDMEX`, más análisis de estructura, sin depender de una sola palabra.
- Preprocesamiento OpenCV: orientación, corrección de perspectiva, reducción de ruido, contraste CLAHE y umbral adaptativo; ensayar variantes con una línea base medida.
- OCR especializado por zonas y PSM Tesseract: anverso, reverso y zona MRZ; usar Docling si aporta resultado en el tipo documental y formato.
- Extracción determinista por expresiones regulares (CURP, fechas, campos etiquetados), parser MRZ cuando esté presente.
- Extracción de respaldo mediante Qwen/Ollama con respuesta Pydantic, contexto del OCR y campos nulos si faltan pruebas en la imagen. El LLM nunca debe completar datos por imaginación.
- Fusión **con prioridad documentada**, validación semántica, trazabilidad de fuente por campo y revisión humana. Evaluar errores por etapas: OCR, clasificación, extracción, validación, mapeo y persistencia.
- Crear corpus autorizado, medir exactitud por campo, falsos rechazos, campos faltantes, latencia, consumo y tasa de correcciones; sin métricas no declarar mejoras cuantitativas.

## Ruta recomendada para preparar Azure (sin recursos creados)

| Pieza lógica | Alternativa Azure que debe evaluarse | Condición de integración |
|---|---|---|
| Frontend Astro compilado / Nginx | Azure Static Web Apps o Container Apps con Nginx | Dominio propio, HTTPS, API proxy y CORS restringido |
| API Express | Azure Container Apps privada o con ingreso controlado | Imagen de contenedor, secretos, healthcheck, JWT cookies, límite de carga |
| Servicio Flask + OCR | Container Apps interno o VM según CPU/memoria | Librerías OCR y aislamiento, colas, tiempo máximo |
| Ollama/Qwen | VM con GPU o perfil GPU compatible de Container Apps | Pruebas de VRAM, modelo, cuotas, costos, latencia; no asumir disponibilidad regional |
| PostgreSQL | Azure Database for PostgreSQL Flexible Server | Red privada/VNet, backups PITR, credenciales administradas |
| MongoDB 7 | MongoDB Atlas en Azure o servicio Mongo autogestionado | Probar compatibilidad de cifrado, índices y operaciones reales |
| Documentos cifrados | Azure Blob Storage con cifrado de aplicación donde corresponda | Acceso privado, política de retención, versionado y borrado |
| Secretos | Azure Key Vault | Rotación de claves y acceso de identidad administrada |
| Contenedores | Azure Container Registry | Imágenes reproducibles, escaneo, etiquetas inmutables |
| Observabilidad | Azure Monitor / Application Insights | Métricas sin PII ni contenido de documentos |

Orden antes de despliegue: validar WSL/Docker; compilar y ejecutar `tt-main` completo; probar registro y OCR con datos sintéticos; migraciones y restauración; estabilizar endpoint de perfil; pruebas E2E extensión; crear recursos Azure y redes; pasar secretos a Key Vault; ejecutar smoke tests y pruebas de restauración; revisar costo y cuotas del LLM.

Referencias oficiales para contrastar infraestructura: Microsoft Learn (Azure Container Apps, GPU, PostgreSQL Flexible Server) y Chrome Developers (MV3 permissions/scripting). Nunca afirmar que una herramienta instalada en local es por sí sola un despliegue en Azure.
