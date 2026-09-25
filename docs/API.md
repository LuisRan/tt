# API REST

Base: `https://localhost:8443/api`. Todas las respuestas son JSON salvo los archivos y el CSV.
La sesión viaja en la cookie `httpOnly` `sesion`; el navegador la envía sola (`credentials: 'same-origin'`).

Convenciones de error: `{ "error": "mensaje", "codigo": "CODIGO", "detalles"?: [...] }`.

| Código HTTP | Significado |
|---|---|
| 400 | Datos inválidos (`VALIDACION`, con `detalles` por campo). |
| 401 | Sin sesión o sesión expirada / revocada. |
| 403 | Rol insuficiente o cuenta deshabilitada. |
| 404 | No existe o no pertenece al usuario (RN-10). |
| 409 | Conflicto (correo ya registrado, documento en estado incorrecto). |
| 415 | Formato de archivo no soportado. |
| 428 | `REQUIERE_CONTRASENA`: reconfirmar con `POST /auth/confirmar-password` y reintentar. |
| 429 | Demasiadas peticiones (Nginx / backend). |
| 502 / 503 | El módulo de IA no respondió o no está disponible. |

Las rutas marcadas con **R** exigen reautenticación reciente (`REAUTH_MINUTOS`, 10 min por defecto).

## Autenticación (`/auth`)

| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| POST | `/auth/registro` | `{correo, password}` | `{requiereCodigo}`; envía código si la verificación está activa. |
| POST | `/auth/registro/verificar` | `{correo, codigo}` | Abre la sesión de registro (cookie `registro`). |
| POST | `/auth/registro/reenviar` | `{correo}` | Reenvía el código. |
| GET | `/auth/registro/estado` | | `{correo, terminos_aceptados}` del registro en curso. |
| POST | `/auth/registro/terminos` | `{acepta: boolean}` | `true`: completa el registro, abre sesión y devuelve `{redirigir: "/app/tutorial"}`. `false`: cancela y borra la cuenta. |
| POST | `/auth/login` | `{correo, password}` | Sin 2FA: `{requiere2fa: false, rol, redirigir}` y sesión abierta. Con 2FA: `{requiere2fa: true}` y código enviado. |
| POST | `/auth/login/verificar` | `{codigo}` | Abre la sesión tras el 2FA. |
| POST | `/auth/login/reenviar` | | Reenvía el código 2FA. |
| POST | `/auth/confirmar-password` | `{password}` | Emite la cookie `reauth` para operaciones **R**. |
| POST | `/auth/logout` | | Cierra la sesión actual. |
| POST | `/auth/logout-todo` | | Revoca todas las sesiones (`token_version`). |
| GET | `/auth/yo` | | `{id, correo, rol, nombre, apellido_paterno, verificado, dosfa_activo, tutorial_visto}` |

## Documentos (`/documentos`) — rol usuario

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/documentos/resumen` | Totales por estado, vigentes y próximos a vencer. |
| GET | `/documentos` | Lista de documentos del usuario (incluye `identificador`, p. ej. `0001_PBC`, en los validados). |
| POST **R** | `/documentos` | multipart: `tipo` (`ine`, `curp`, `acta_nacimiento`, `pasaporte`) + `archivo` (PDF/JPG/PNG). Responde 202 y se procesa en segundo plano. |
| GET | `/documentos/:id` | Detalle: estado, datos detectados/confirmados, advertencias, métricas, historial (`validaciones` con `origen`). |
| GET | `/documentos/:id/estado` | Estado y etapa actual (para sondeo). |
| POST **R** | `/documentos/:id/validar` | `{datos: {campo: valor}}`. Confirma la primera vez (cuenta para la precisión). Asigna el folio y devuelve `identificador`. |
| PUT **R** | `/documentos/:id/datos` | `{datos: {...}}`. Edita un documento ya validado; no cuenta para la precisión. Devuelve `campos_modificados`. |
| POST | `/documentos/:id/procesar` | Reintenta un documento con error. |
| DELETE | `/documentos/:id` | Descarta un documento que no esté validado ni en proceso. |
| GET | `/documentos/:id/archivo-url` | URL firmada de corta duración para ver el archivo original. |
| GET | `/archivos/:token` | Descarga el archivo descifrado usando la URL firmada. |

## Perfil (`/perfil`) — cualquier sesión

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/perfil` | Datos personales (descifrados), preferencias, `dosfa_activo`, `tutorial_visto`, `editado_manual`. |
| PUT **R** | `/perfil` | Edita `nombre`, `apellido_paterno`, `apellido_materno`, `curp`, `fecha_nacimiento` (dd/mm/aaaa o ISO), `sexo` (H/M), `direccion`. |
| PUT | `/perfil/preferencias` | `{alertas_vigencia}` |
| PUT **R** | `/perfil/seguridad` | `{dosfa_activo}` activa o desactiva la verificación en dos pasos. |
| POST | `/perfil/tutorial` | Marca la guía de uso como vista. |
| POST | `/perfil/password` | `{actual, nueva}`; revoca todas las sesiones (hay que volver a entrar). |
| GET | `/perfil/autofill` | Datos listos para autocompletar formularios (uso por integraciones; no visible en la interfaz). |

## Administración (`/admin`) — rol administrador

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/admin/resumen` | KPIs del dashboard. |
| GET | `/admin/estado` | Estado de PostgreSQL, MongoDB, módulo de IA (plataforma, modelo) y cola. |
| GET | `/admin/usuarios?pagina&por_pagina&buscar&estado` | `estado`: `todos`, `activos`, `inactivos`, `pendientes`. |
| POST | `/admin/usuarios` | `{correo, password, rol, activo?, dosfa_activo?, nombre?, ...}` |
| GET | `/admin/usuarios/:id` | Ficha completa (CURP enmascarada). |
| PUT | `/admin/usuarios/:id` | Campos de cuenta (`correo`, `rol`, `activo`, `dosfa_activo`) y de perfil. Solo se envían los que cambian. |
| POST | `/admin/usuarios/:id/password` | `{password}` |
| DELETE | `/admin/usuarios/:id` | Baja lógica. Con `?definitivo=true`, eliminación permanente. |
| POST | `/admin/usuarios/:id/reactivar` | |
| GET | `/admin/trafico?dias=30` | Series diarias, totales (`precision_campos`, `campos_evaluados`, `tiempo_promedio_ms`, `confianza_promedio`, `errores_ia`, `registros`), documentos por tipo y rutas más usadas. |
| GET | `/admin/logs?...` | Eventos y accesos filtrables y paginados. |
| GET | `/admin/reporte?...` | CSV con los mismos filtros. |
| GET | `/admin/respaldos` | Últimos respaldos. |

## Extensión de navegador (`/extension`)

| Método | Ruta | Autenticación | Descripción |
|---|---|---|---|
| GET | `/extension/info` | sesión | `{extension_id, version, url}` |
| POST **R** | `/extension/paquete` | sesión | `.zip` de la extensión personalizada (URL + código de vinculación de 15 min). |
| POST **R** | `/extension/codigo` | sesión | `{codigo: "XXXX-XXXX", expira_en, url}` para vincular a mano. |
| GET | `/extension/dispositivos` | sesión | Extensiones vinculadas. |
| DELETE | `/extension/dispositivos/:id` | sesión | Desvincula una extensión. |
| POST | `/extension/vincular` | código | `{codigo, nombre}` → `{token, correo}`. Un solo uso; limitado a 20 intentos / 15 min. |
| GET | `/extension/documentos` | Bearer | Solo documentos **validados**: `identificador`, `tipo`, `titular`, `vigente`, `fecha_vigencia`, `campos[{campo, etiqueta, valor}]`, `texto` (.txt) y `texto_escaneado` (OCR). |
| POST | `/extension/desvincular` | Bearer | La extensión revoca su propio token. |

Las rutas con token Bearer o código no usan cookies, por lo que quedan fuera de la verificación
de origen (CSRF) que se aplica al resto de peticiones que modifican datos.

## Salud

`GET /api/salud` → estado del backend y sus dependencias.

## Módulo de IA (red interna, `X-API-Key`)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/documentos/procesa` | multipart `archivo` (PDF), `user_id`, `doc_id`. Devuelve tipo, campos, confianza, advertencias, `metadatos.tiempos_etapas_ms`, `campos_corregidos_ocr`. |
| GET | `/api/v1/documentos/<doc_id>` | Resultado guardado. |
| GET | `/api/v1/documentos/<doc_id>/texto` | Texto OCR (markdown si `Accept: text/markdown`). |
| GET | `/api/v1/usuarios/<user_id>/documentos` | Documentos procesados de un usuario. |
| GET | `/health` | Plataforma detectada, motor OCR, modelo LLM y disponibilidad. |

El backend convierte JPG/PNG a PDF (pdf-lib) antes de enviarlos, porque el módulo solo acepta PDF.
