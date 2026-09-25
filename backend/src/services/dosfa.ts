/**
 * Autenticación de dos factores por correo (sección 2.7.2.8, RNF-02).
 *
 * Algoritmo:
 *  1. Se genera un código de 6 dígitos con CSPRNG (crypto.randomInt).
 *  2. Se guarda SOLO su HMAC-SHA256 (ligado al usuario y al propósito) con
 *     expiración; los códigos anteriores no usados se invalidan.
 *  3. Se envía por correo con Resend.
 *  4. Al verificar se recalcula el HMAC del código ingresado y se compara en
 *     tiempo constante. Máximo N intentos; el código es de un solo uso.
 */
import { config } from '../config.js';
import { query, uno } from '../db/pg.js';
import { generarCodigo, hmac, igualSeguro } from '../lib/crypto.js';
import { ErrorApp } from '../lib/errores.js';
import { enviarCorreo } from './correo.js';
import { correoCodigo } from './plantillas-correo.js';

export type Proposito = 'registro' | 'login' | 'sensible';

const huella = (usuarioId: number, proposito: Proposito, codigo: string) =>
  hmac(`2fa:${usuarioId}:${proposito}:${codigo}`);

export async function emitirCodigo(usuarioId: number, correo: string, proposito: Proposito): Promise<void> {
  // Anti-abuso: máximo 1 código cada 30 s por usuario/propósito
  const reciente = await uno<{ n: number }>(
    `SELECT COUNT(*)::int AS n FROM codigos_2fa
      WHERE usuario_id = $1 AND proposito = $2 AND creado_en > NOW() - INTERVAL '30 seconds'`,
    [usuarioId, proposito],
  );
  if (reciente && reciente.n > 0) {
    throw new ErrorApp(429, 'ESPERA_REENVIO', 'Espera unos segundos antes de solicitar otro código');
  }

  const codigo = generarCodigo(6);
  await query(`UPDATE codigos_2fa SET usado = TRUE WHERE usuario_id = $1 AND proposito = $2 AND NOT usado`, [
    usuarioId,
    proposito,
  ]);
  await query(
    `INSERT INTO codigos_2fa (usuario_id, codigo, proposito, expiracion)
     VALUES ($1, $2, $3, NOW() + make_interval(mins => $4))`,
    [usuarioId, huella(usuarioId, proposito, codigo), proposito, config.CODIGO_2FA_MINUTOS],
  );
  const plantilla = correoCodigo(codigo, proposito, config.CODIGO_2FA_MINUTOS);
  await enviarCorreo({ para: correo, ...plantilla });
}

export async function verificarCodigo(usuarioId: number, proposito: Proposito, codigo: string): Promise<boolean> {
  const limpio = (codigo ?? '').replace(/\D/g, '');
  const fila = await uno<{ id: number; codigo: string; intentos: number; vencido: boolean }>(
    `SELECT id, codigo, intentos, (expiracion < NOW()) AS vencido
       FROM codigos_2fa
      WHERE usuario_id = $1 AND proposito = $2 AND NOT usado
      ORDER BY creado_en DESC LIMIT 1`,
    [usuarioId, proposito],
  );
  if (!fila) throw new ErrorApp(400, 'CODIGO_INEXISTENTE', 'No hay un código activo. Solicita uno nuevo.');
  if (fila.vencido) {
    await query('UPDATE codigos_2fa SET usado = TRUE WHERE id = $1', [fila.id]);
    throw new ErrorApp(400, 'CODIGO_EXPIRADO', 'El código expiró. Solicita uno nuevo.');
  }
  if (fila.intentos >= config.CODIGO_2FA_INTENTOS) {
    await query('UPDATE codigos_2fa SET usado = TRUE WHERE id = $1', [fila.id]);
    throw new ErrorApp(429, 'DEMASIADOS_INTENTOS', 'Demasiados intentos. Solicita un código nuevo.');
  }

  const ok = limpio.length === 6 && igualSeguro(huella(usuarioId, proposito, limpio), fila.codigo);
  if (ok) {
    await query('UPDATE codigos_2fa SET usado = TRUE WHERE id = $1', [fila.id]);
  } else {
    await query('UPDATE codigos_2fa SET intentos = intentos + 1 WHERE id = $1', [fila.id]);
  }
  return ok;
}
