// @ts-check
import { defineConfig } from 'astro/config';

// Sitio estático: Nginx lo sirve y hace de reverse proxy hacia /api (backend).
// En desarrollo (npm run dev) Vite reenvía /api al backend local.
export default defineConfig({
  output: 'static',
  trailingSlash: 'ignore',
  // Sin scripts/estilos en línea: permite una CSP estricta (script-src 'self') en Nginx
  build: { format: 'directory', inlineStylesheets: 'never' },
  server: { port: 4321, host: true },
  vite: {
    // top-level await en los scripts de las páginas
    build: { target: 'es2022', assetsInlineLimit: 0 },
    server: {
      proxy: {
        '/api': { target: process.env.BACKEND_URL ?? 'http://localhost:3000', changeOrigin: false },
      },
    },
  },
});
