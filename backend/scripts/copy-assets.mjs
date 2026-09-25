// Copia archivos no-TS (schema.sql) a dist/ después de compilar.
import { cpSync, mkdirSync } from 'node:fs';
mkdirSync('dist/db', { recursive: true });
cpSync('src/db/schema.sql', 'dist/db/schema.sql');
console.log('assets copiados');
