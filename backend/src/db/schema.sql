-- ════════════════════════════════════════════════════════════════════════════
-- Esquema relacional (PostgreSQL 16) — Capítulo 7 del documento del TT
-- Basado en el diagrama de la base de datos (Fig. 31) y el diagrama de clases
-- (Fig. 26). Es idempotente: el backend lo ejecuta en cada arranque.
--
-- Datos sensibles (CURP, dirección, valores extraídos) se guardan CIFRADOS con
-- AES-256-GCM desde el backend (columnas *_cifrado / valor_*). Las contraseñas
-- se guardan con bcrypt (hash + salt). Los códigos 2FA se guardan como HMAC.
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS usuarios (
    id                    SERIAL PRIMARY KEY,
    correo                VARCHAR(254) NOT NULL UNIQUE,
    password_hash         TEXT         NOT NULL,
    rol                   VARCHAR(20)  NOT NULL DEFAULT 'usuario'
                          CHECK (rol IN ('usuario', 'administrador')),
    activo                BOOLEAN      NOT NULL DEFAULT TRUE,
    correo_verificado     BOOLEAN      NOT NULL DEFAULT FALSE,
    registro_completo     BOOLEAN      NOT NULL DEFAULT FALSE,
    terminos_aceptados_en TIMESTAMPTZ,
    alertas_vigencia      BOOLEAN      NOT NULL DEFAULT TRUE,
    token_version         INTEGER      NOT NULL DEFAULT 0,   -- revocación de sesiones (R10)
    ultimo_acceso         TIMESTAMPTZ,
    creado_en             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    actualizado_en        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS perfil_usuario (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre             VARCHAR(120),
    apellido_paterno   VARCHAR(80),
    apellido_materno   VARCHAR(80),
    curp               TEXT,           -- cifrado AES-256-GCM
    curp_hash          VARCHAR(64),    -- HMAC para búsquedas/unicidad sin descifrar
    fecha_nacimiento   DATE,
    sexo               VARCHAR(1),
    direccion          TEXT,           -- cifrado AES-256-GCM
    verificado         BOOLEAN NOT NULL DEFAULT FALSE,
    actualizado_en     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tipos_documento (
    id                 SERIAL PRIMARY KEY,
    clave              VARCHAR(30)  NOT NULL UNIQUE,   -- coincide con el módulo de IA
    nombre             VARCHAR(80)  NOT NULL,
    descripcion        TEXT,
    tiene_vigencia     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS documentos (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo_id            INTEGER REFERENCES tipos_documento(id),
    ruta_archivo       TEXT    NOT NULL,
    nombre_original    VARCHAR(255),
    mime_original      VARCHAR(80),
    tamano_bytes       INTEGER,
    hash_sha256        VARCHAR(64),
    estado             VARCHAR(20) NOT NULL DEFAULT 'cargado'
                       CHECK (estado IN ('cargado','procesando','procesado','validado','rechazado','error')),
    version            INTEGER NOT NULL DEFAULT 1,
    vigente            BOOLEAN NOT NULL DEFAULT FALSE,  -- RN-07: la última versión validada
    mongo_resultado_id VARCHAR(24),
    ia_doc_id          VARCHAR(64) UNIQUE,
    fecha_vigencia     DATE,          -- RN-11: fecha de expiración del documento
    motivo_rechazo     TEXT,
    advertencias       JSONB NOT NULL DEFAULT '[]'::jsonb,
    intentos           INTEGER NOT NULL DEFAULT 0,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_documentos_usuario ON documentos(usuario_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_documentos_estado  ON documentos(estado);
CREATE INDEX IF NOT EXISTS idx_documentos_vigencia ON documentos(fecha_vigencia) WHERE vigente;

CREATE TABLE IF NOT EXISTS documento_versiones (
    id                 SERIAL PRIMARY KEY,
    documento_id       INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo_id            INTEGER REFERENCES tipos_documento(id),
    version            INTEGER NOT NULL,
    ruta_archivo       TEXT    NOT NULL,
    fecha              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_versiones_usuario_tipo ON documento_versiones(usuario_id, tipo_id, version DESC);

-- ValidacionDato (Fig. 26): valor detectado vs. valor confirmado por el usuario (RN-08, RN-09)
CREATE TABLE IF NOT EXISTS validaciones_dato (
    id                 SERIAL PRIMARY KEY,
    documento_id       INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    campo              VARCHAR(60) NOT NULL,
    valor_detectado    TEXT,          -- cifrado
    valor_confirmado   TEXT,          -- cifrado
    corregido          BOOLEAN NOT NULL DEFAULT FALSE,
    validado           BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_validaciones_documento ON validaciones_dato(documento_id);

CREATE TABLE IF NOT EXISTS metricas_ia (
    id                    SERIAL PRIMARY KEY,
    documento_id          INTEGER REFERENCES documentos(id) ON DELETE CASCADE,
    tiempo_procesamiento  INTEGER,         -- ms reportados por el módulo de IA
    tiempo_total          INTEGER,         -- ms medidos por el backend (incluye red/cola)
    modelo_usado          VARCHAR(80),
    adaptador             VARCHAR(80),
    confianza             NUMERIC(4,2),
    campos_detectados     INTEGER,
    alucinaciones         INTEGER NOT NULL DEFAULT 0,
    exito                 BOOLEAN NOT NULL DEFAULT TRUE,
    codigo_http           INTEGER,
    error                 TEXT,
    creado_en             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_metricas_fecha ON metricas_ia(creado_en);

CREATE TABLE IF NOT EXISTS codigos_2fa (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    codigo             VARCHAR(64) NOT NULL,   -- HMAC-SHA256 del código, nunca el código en claro
    proposito          VARCHAR(20) NOT NULL DEFAULT 'login'
                       CHECK (proposito IN ('registro','login','sensible')),
    expiracion         TIMESTAMPTZ NOT NULL,
    usado              BOOLEAN NOT NULL DEFAULT FALSE,
    intentos           INTEGER NOT NULL DEFAULT 0,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_codigos_usuario ON codigos_2fa(usuario_id, proposito, usado);

CREATE TABLE IF NOT EXISTS accesos (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    correo_intentado   VARCHAR(254),
    ip                 VARCHAR(64),
    user_agent         TEXT,
    tipo               VARCHAR(20) NOT NULL,   -- login | 2fa | logout | intento | registro
    exito              BOOLEAN NOT NULL,
    fecha              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_accesos_fecha ON accesos(fecha);

CREATE TABLE IF NOT EXISTS eventos_sistema (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    tipo_evento        VARCHAR(20) NOT NULL,   -- info | advertencia | error | seguridad | auditoria
    tabla_afectada     VARCHAR(60),
    operacion          VARCHAR(60),
    descripcion        TEXT,
    ip                 VARCHAR(64),
    fecha              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eventos_fecha ON eventos_sistema(fecha);

CREATE TABLE IF NOT EXISTS respaldos (
    id                 SERIAL PRIMARY KEY,
    tipo               VARCHAR(20) NOT NULL,   -- postgres | mongo | archivos
    ubicacion          TEXT NOT NULL,
    tamano             BIGINT,
    estado             VARCHAR(20) NOT NULL DEFAULT 'completado',
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Mapeo campo de formulario web -> campo de la BD (autocompletado de formularios externos)
CREATE TABLE IF NOT EXISTS plantillas_autofill (
    id                 SERIAL PRIMARY KEY,
    campo_web          VARCHAR(80) NOT NULL UNIQUE,
    campo_bd           VARCHAR(80) NOT NULL,
    descripcion        TEXT
);

-- RN-12: registro de alertas de vigencia enviadas (evita duplicados)
CREATE TABLE IF NOT EXISTS alertas_vigencia (
    id                 SERIAL PRIMARY KEY,
    documento_id       INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    dias_antes         INTEGER NOT NULL,
    enviada_en         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (documento_id, dias_antes)
);

-- Tráfico de la API agregado por hora (RF-ADM-04)
CREATE TABLE IF NOT EXISTS trafico_api (
    hora               TIMESTAMPTZ NOT NULL,
    metodo             VARCHAR(8)  NOT NULL,
    ruta               VARCHAR(80) NOT NULL,
    estado             VARCHAR(3)  NOT NULL,   -- 2xx | 4xx | 5xx
    total              INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (hora, metodo, ruta, estado)
);

-- ── Catálogos ────────────────────────────────────────────────────────────────
INSERT INTO tipos_documento (clave, nombre, descripcion, tiene_vigencia) VALUES
  ('ine',             'INE',                'Credencial para Votar, formato vigente (2016+)',      TRUE),
  ('curp',            'CURP',               'Constancia de la CURP digital (formato 2019, máx. 3 meses)', FALSE),
  ('acta_nacimiento', 'Acta de nacimiento', 'Formato Único Nacional (2015+)',                     FALSE),
  ('pasaporte',       'Pasaporte',          'Pasaporte mexicano electrónico tipo E (2021+)',       TRUE)
ON CONFLICT (clave) DO NOTHING;

INSERT INTO plantillas_autofill (campo_web, campo_bd, descripcion) VALUES
  ('given-name',      'perfil.nombre',           'Nombre(s) (autocomplete HTML estándar)'),
  ('family-name',     'perfil.apellido_paterno', 'Primer apellido'),
  ('additional-name', 'perfil.apellido_materno', 'Segundo apellido'),
  ('name',            'perfil.nombre_completo',  'Nombre completo'),
  ('bday',            'perfil.fecha_nacimiento', 'Fecha de nacimiento (AAAA-MM-DD)'),
  ('sex',             'perfil.sexo',             'Sexo (H/M)'),
  ('street-address',  'perfil.direccion',        'Domicilio'),
  ('email',           'usuario.correo',          'Correo electrónico'),
  ('curp',            'perfil.curp',             'CURP (campo no estándar usado en trámites MX)')
ON CONFLICT (campo_web) DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════════
-- Migraciones incrementales (idempotentes)
-- ════════════════════════════════════════════════════════════════════════════
-- 2FA opcional por usuario (se activa desde Configuración)
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS dosfa_activo   BOOLEAN NOT NULL DEFAULT FALSE;
-- Tutorial inicial obligatorio la primera vez
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tutorial_visto BOOLEAN NOT NULL DEFAULT FALSE;
-- 'validacion' = primera confirmación del usuario (mide la precisión de la IA)
-- 'edicion'    = cambios posteriores del usuario (NO cuentan para la métrica)
ALTER TABLE validaciones_dato ADD COLUMN IF NOT EXISTS origen VARCHAR(12) NOT NULL DEFAULT 'validacion';
-- Perfil editado a mano por el usuario o por un administrador
ALTER TABLE perfil_usuario ADD COLUMN IF NOT EXISTS editado_manual BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS editado_en TIMESTAMPTZ;
-- Los administradores existentes no necesitan tutorial de usuario
UPDATE usuarios SET tutorial_visto = TRUE WHERE rol = 'administrador' AND NOT tutorial_visto;

-- Folio por usuario: orden en que se confirmó cada documento (0001, 0002…).
-- Junto con las iniciales del titular forma el identificador que ve el usuario
-- en la extensión (p. ej. 0001_CSC).
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS folio INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documentos_folio ON documentos(usuario_id, folio) WHERE folio IS NOT NULL;
-- Asigna folio a los documentos ya validados de usuarios que aún no tienen ninguno
UPDATE documentos d SET folio = n.f
  FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY usuario_id ORDER BY creado_en, id) AS f
          FROM documentos
         WHERE estado = 'validado'
           AND usuario_id NOT IN (SELECT usuario_id FROM documentos WHERE folio IS NOT NULL)) n
 WHERE d.id = n.id;

-- ── Extensión de navegador ──────────────────────────────────────────────────
-- Códigos de vinculación de un solo uso (15 min). Se guarda solo su HMAC.
CREATE TABLE IF NOT EXISTS extension_codigos (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    codigo_hash        VARCHAR(64) NOT NULL UNIQUE,
    expira_en          TIMESTAMPTZ NOT NULL,
    usado              BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Extensiones vinculadas. El token (Bearer) solo se guarda como HMAC; token_version
-- replica el de la cuenta: cambiar la contraseña o "cerrar todas las sesiones" las revoca.
CREATE TABLE IF NOT EXISTS extension_dispositivos (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre             VARCHAR(80),
    token_hash         VARCHAR(64) NOT NULL UNIQUE,
    token_version      INTEGER NOT NULL,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ultimo_uso         TIMESTAMPTZ,
    ultima_ip          VARCHAR(64),
    revocado_en        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ext_disp_usuario ON extension_dispositivos(usuario_id);
-- Iniciales del titular (se calculan al validar/editar a partir de los datos confirmados)
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS titular_iniciales VARCHAR(4);
-- Titular del documento (HMAC de su CURP o, si no tiene, de su nombre). La versión
-- vigente (RN-07) se lleva por usuario + tipo + TITULAR: validar la INE de otra
-- persona (pruebas) no convierte la INE propia en "versión anterior".
ALTER TABLE documentos          ADD COLUMN IF NOT EXISTS titular_hash VARCHAR(64);
ALTER TABLE documento_versiones ADD COLUMN IF NOT EXISTS titular_hash VARCHAR(64);
