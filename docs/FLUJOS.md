# Flujos de la plataforma

## Usuario

### Registro (CUP-01)
1. **Cuenta**: correo + contraseña (mínimo 10 caracteres, mayúscula, minúscula y número).
2. **Verificar correo**: código de 6 dígitos enviado por correo (se puede desactivar con
   `REGISTRO_VERIFICAR_CORREO=false`, útil en pruebas locales).
3. **Términos y aviso de privacidad**: si no se aceptan, el registro se cancela y se borra lo capturado.
4. Al aceptar se abre la sesión y se muestra la **Guía de uso**.

Ya **no se exige subir la INE al registrarse**: el usuario puede cargar documentos cuando quiera.

### Guía de uso (tutorial)
- Ocho pasos: bóveda, subir, procesamiento, validar, consultar, alertas, extensión y configuración.
- **No se puede saltar** la primera vez: cualquier página de `/app` redirige a `/app/tutorial`
  hasta llegar al último paso (`POST /api/perfil/tutorial` marca `usuarios.tutorial_visto`).
- Después queda disponible en la pestaña **Guía de uso** (arriba a la derecha).
- Los administradores no ven el tutorial.

### Inicio de sesión y verificación en dos pasos (CUP-02)
- Por defecto **ningún usuario** (incluido el administrador) tiene 2FA: se entra con correo y contraseña.
- Cada usuario la activa o desactiva en **Configuración > Verificación en dos pasos**
  (`PUT /api/perfil/seguridad`, pide reconfirmar la contraseña). El administrador también puede
  cambiarla desde la ficha de un usuario.
- Con 2FA activa, tras la contraseña se envía un código de 6 dígitos (10 min, 5 intentos).

### Subir y procesar un documento (CUP-03, CUP-04)
1. **Procesamiento OCR**: elegir tipo (INE, CURP, acta, pasaporte) y archivo PDF/JPG/PNG ≤ 10 MB.
2. Se pide reconfirmar la contraseña (operación sensible).
3. El documento se procesa en segundo plano; la pantalla muestra las etapas y el tiempo.
4. Si el tipo detectado no coincide con el elegido, o no cumple una regla (CURP de más de 3 meses,
   formato no soportado, manuscrito), el documento queda **Rechazado** con el motivo.

### Validar (CUP-05)
- Se muestran los campos detectados; los obligatorios llevan asterisco.
- Los campos corregidos automáticamente por validación cruzada se marcan para revisarlos.
- Al confirmar, el documento queda **Validado** y como versión vigente de su tipo.

### Documentos de otra persona (pruebas)
Un usuario puede procesar documentos que no son suyos (por ejemplo, para probar con varias INE sin
crear cuentas nuevas). El perfil solo se completa cuando:
- el perfil aún no tiene CURP, o
- el documento no trae CURP, o
- la CURP del documento coincide con la del perfil.

En los demás casos el documento se guarda igual, pero **no sobrescribe** los datos del titular y se
muestra el aviso "El documento pertenece a otra persona...". Los campos del perfil que ya tenían
valor nunca se reemplazan con los de otro documento (solo el domicilio se actualiza con la INE del
propio titular).

### Identificador de cada documento
Al confirmar un documento se le asigna un identificador como **`0001_CSC`**: el número es el orden en
que el usuario lo confirmó (0001 el primero) y las letras son las iniciales del titular (nombre,
primer y segundo apellido). Se muestra en **Documentos**, al terminar la validación y en la extensión.

### Editar un documento ya validado
- En **Documentos > (documento) > Editar información**.
- Pide reconfirmar la contraseña; guarda los cambios en MongoDB y registra cada campo modificado en
  `validaciones_dato` con `origen = 'edicion'`.
- **No afecta la métrica de precisión de la IA**, que solo considera la primera validación
  (`origen = 'validacion'`).
- El historial del documento muestra qué se corrigió al validar y qué se editó después.

### Extensión de navegador
Menú **Extensión** (`/app/extension`):
1. **Descargar e instalar extensión**: la plataforma genera un `.zip` personalizado (URL de la
   plataforma + código de vinculación de 15 min). Se descomprime y se carga en
   `chrome://extensions` con *Modo de desarrollador > Cargar descomprimida*.
2. Al cargarla **se vincula sola** y sincroniza los documentos validados. La página detecta la
   extensión y muestra su estado (instalada, vinculada, documentos, última sincronización).
3. Si ya estaba instalada o el código venció: **Vincular ahora** (la página le envía un código nuevo)
   o **Generar código** para escribirlo en la extensión.
4. **Extensiones vinculadas**: lista y desvinculación.

En la extensión: inicio con **Sincronizar** y las 4 categorías; en cada categoría los documentos
`0001_XXX`; al abrir uno, sus datos con **copiar** por campo, **Copiar todo** y **Descargar .txt**.
Solo aparecen documentos **validados**. El texto OCR ya no se muestra en la plataforma; queda
disponible (plegado) en la extensión.

### Configuración
- **Mi información**: editar nombre, apellidos, CURP, fecha de nacimiento, sexo y domicilio
  (pide reconfirmar la contraseña; se marca como "Editado manualmente").
- Verificación en dos pasos, alertas de vigencia, cambio de contraseña y cierre de todas las sesiones.
- El autocompletado de formularios existe como endpoint (`GET /api/perfil/autofill`) para
  integraciones, pero **no se muestra** en la interfaz.

### Alertas de vigencia (RN-11, RN-12)
Tarea diaria (`ALERTAS_CRON`) que envía correo 90, 30 y 7 días antes y el día del vencimiento de
cada documento vigente con fecha de expiración, si el usuario tiene las alertas activas.

## Administrador

### Usuarios (CRUD completo, CUA-03..05)
| Acción | Endpoint | Notas |
|---|---|---|
| Listar / buscar / filtrar | `GET /api/admin/usuarios` | Paginado; filtros por estado y texto. |
| Crear | `POST /api/admin/usuarios` | Correo, contraseña inicial, rol, 2FA y datos personales opcionales. Queda con correo verificado. |
| Consultar | `GET /api/admin/usuarios/:id` | Cuenta, perfil (CURP enmascarada), documentos y últimos accesos. |
| Editar | `PUT /api/admin/usuarios/:id` | Correo, rol, 2FA y datos personales. Cambiar rol/correo o deshabilitar revoca sus sesiones. El admin no puede quitarse su propio rol ni deshabilitarse. |
| Restablecer contraseña | `POST /api/admin/usuarios/:id/password` | Revoca sesiones. |
| Deshabilitar (baja lógica) | `DELETE /api/admin/usuarios/:id` | Conserva datos; se puede reactivar. |
| Reactivar | `POST /api/admin/usuarios/:id/reactivar` | |
| Eliminar definitivamente | `DELETE /api/admin/usuarios/:id?definitivo=true` | Borra archivos, resultados en MongoDB y registros. |

Todas las acciones quedan en `eventos_sistema` (auditoría).

### Tráfico, precisión y logs (CUA-06)
- **Precisión de la IA** = porcentaje de campos que el usuario confirmó **sin corregir** en la
  primera validación. Las ediciones posteriores no cuentan.
- Tiempo promedio de procesamiento, confianza promedio, errores del módulo, registros nuevos,
  gráficas diarias, documentos por tipo y rutas más usadas.
- Logs de eventos y reporte CSV.
