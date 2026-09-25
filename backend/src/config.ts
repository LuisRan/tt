/**
 * Configuración centralizada. Todo sale de variables de entorno (.env en la
 * raíz del monorepo o en backend/). Se valida al arrancar para fallar rápido
 * con un mensaje claro en lugar de errores raros en tiempo de ejecución.
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { z } from 'zod';

const aqui = path.dirname(fileURLToPath(import.meta.url));
// backend/.env tiene prioridad; luego el .env de la raíz del monorepo
for (const candidato of [path.resolve(aqui, '../.env'), path.resolve(aqui, '../../.env')]) {
  if (existsSync(candidato)) dotenv.config({ path: candidato, override: false });
}

const bool = z
  .union([z.boolean(), z.string()])
  .transform((v) => (typeof v === 'boolean' ? v : ['1', 'true', 'si', 'sí', 'yes', 'on'].includes(v.toLowerCase())));

const esquema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.coerce.number().int().default(3000),
  APP_NAME: z.string().default('OCR Platform'),
  PUBLIC_URL: z.string().url().default('https://localhost:8443'),
  LOG_LEVEL: z.string().default('info'),

  // PostgreSQL
  DATABASE_URL: z.string().min(1, 'DATABASE_URL es obligatorio'),
  // MongoDB (resultados OCR semiestructurados)
  MONGO_URL: z.string().min(1, 'MONGO_URL es obligatorio'),
  MONGO_DB: z.string().default('tt_ocr'),

  // Seguridad
  JWT_SECRET: z.string().min(32, 'JWT_SECRET debe tener al menos 32 caracteres'),
  JWT_EXPIRA_MIN: z.coerce.number().int().default(120),
  DATA_ENCRYPTION_KEY: z
    .string()
    .refine((v) => Buffer.from(v, 'base64').length === 32, 'DATA_ENCRYPTION_KEY debe ser 32 bytes en base64'),
  COOKIE_SECURE: bool.default(true),
  CODIGO_2FA_MINUTOS: z.coerce.number().int().default(10),
  CODIGO_2FA_INTENTOS: z.coerce.number().int().default(5),
  REAUTH_MINUTOS: z.coerce.number().int().default(10),
  // Verificar el correo con un código al registrarse (el 2FA de inicio de
  // sesión es opcional y cada usuario lo activa en Configuración)
  REGISTRO_VERIFICAR_CORREO: bool.default(true),
  CORS_ORIGINS: z.string().default(''),

  // Administrador inicial
  ADMIN_EMAIL: z.string().email().optional().or(z.literal('')),
  ADMIN_PASSWORD: z.string().optional(),

  // Correo (Resend)
  MAIL_PROVIDER: z.enum(['resend', 'console']).default('console'),
  RESEND_API_KEY: z.string().optional().default(''),
  MAIL_FROM: z.string().default('OCR Platform <onboarding@resend.dev>'),
  MAIL_REPLY_TO: z.string().optional().default(''),

  // Módulo de IA (Flask)
  IA_URL: z.string().url().default('http://localhost:5001'),
  IA_API_KEY: z.string().optional().default(''),
  IA_TIMEOUT_MS: z.coerce.number().int().default(600_000),
  IA_CONCURRENCIA: z.coerce.number().int().min(1).default(1),
  IA_REINTENTOS: z.coerce.number().int().min(0).default(2),

  // Almacenamiento de archivos (local cifrado; Azure Blob en la nube)
  STORAGE_DIR: z.string().default(path.resolve(aqui, '../../data/storage')),
  MAX_UPLOAD_MB: z.coerce.number().default(10),

  // Código fuente de la extensión de navegador (se empaqueta personalizada por usuario)
  EXTENSION_DIR: z.string().default(path.resolve(aqui, '../../extension')),
  EXTENSION_CODIGO_MINUTOS: z.coerce.number().int().min(1).default(15),

  // Reglas de negocio
  CURP_MAX_DIAS: z.coerce.number().int().default(90), // RN-06
  ALERTAS_DIAS: z.string().default('90,30,7,0'), // RN-12
  ALERTAS_CRON: z.string().default('0 9 * * *'),
  TZ: z.string().default('America/Mexico_City'),
});

const resultado = esquema.safeParse(process.env);
if (!resultado.success) {
  const errores = resultado.error.issues.map((i) => `  - ${i.path.join('.')}: ${i.message}`).join('\n');
  // eslint-disable-next-line no-console
  console.error(`\n[config] Variables de entorno inválidas:\n${errores}\n\nRevisa tu archivo .env (ver .env.example).\n`);
  process.exit(1);
}

const env = resultado.data;

if (env.MAIL_PROVIDER === 'resend' && !env.RESEND_API_KEY) {
  // eslint-disable-next-line no-console
  console.warn('[config] MAIL_PROVIDER=resend pero RESEND_API_KEY está vacío: se usará la consola.');
}

export const config = {
  ...env,
  esProduccion: env.NODE_ENV === 'production',
  esTest: env.NODE_ENV === 'test',
  proveedorCorreo: env.MAIL_PROVIDER === 'resend' && env.RESEND_API_KEY ? 'resend' : 'console',
  claveDatos: Buffer.from(env.DATA_ENCRYPTION_KEY, 'base64'),
  alertasDias: env.ALERTAS_DIAS.split(',')
    .map((d) => parseInt(d.trim(), 10))
    .filter((d) => Number.isFinite(d) && d >= 0)
    .sort((a, b) => b - a),
  corsOrigins: env.CORS_ORIGINS.split(',').map((s) => s.trim()).filter(Boolean),
} as const;

export type Config = typeof config;
