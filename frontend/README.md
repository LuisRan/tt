# Frontend (Astro 5, sitio estático)

Interfaz web compilada a HTML/CSS/JS estático que sirve Nginx. Toda la lógica de datos vive en el
backend; las páginas consumen `/api` con `fetch` y cookies `httpOnly`.

## Estructura

```
src/
├── layouts/
│   ├── Base.astro        <head>, estilos globales, contenedor de toasts
│   └── Panel.astro       barra lateral (usuario / admin) y pestaña superior "Guía de uso"
├── components/
│   └── Icono.astro       icono SVG en componentes .astro
├── lib/
│   ├── api.ts            cliente fetch: errores tipados, 401 -> /login, 428 -> pide contraseña y reintenta,
│   │                     requerirSesion() (rol + tutorial obligatorio)
│   ├── ui.ts             toasts, modales, confirmar(), pedirPassword(), formato de fechas, esc()
│   ├── validacion.ts     formulario de campos detectados (modo "validar" y modo "editar"),
│   │                     espera del procesamiento y pasos del proceso
│   ├── iconos.ts         set de iconos SVG de línea (sin emojis) e icono(nombre, tamaño)
│   └── terminos.ts       texto de términos y aviso de privacidad
├── pages/
│   ├── index.astro       inicio público
│   ├── login.astro       correo + contraseña (+ código si el usuario activó 2FA)
│   ├── registro.astro    cuenta -> verificar correo -> términos -> guía de uso
│   ├── terminos.astro
│   ├── app/              área del usuario
│   │   ├── tutorial.astro     guía de 7 pasos, obligatoria la primera vez
│   │   ├── index.astro        resumen, alertas de vigencia
│   │   ├── subir.astro        carga, procesamiento y validación en una sola vista
│   │   ├── documentos.astro   lista con identificador (0001_CSC), detalle, archivo, historial, "Editar información"
│   │   ├── validar.astro      validar (?id=) o editar (?id=&modo=editar)
│   │   ├── validaciones.astro pendientes de validar
│   │   ├── extension.astro    descarga, instalación, detección y vinculación de la extensión
│   │   └── perfil.astro       Configuración: mi información, 2FA, alertas, contraseña, sesiones
│   └── admin/
│       ├── index.astro        dashboard y estado de servicios
│       ├── usuarios.astro     CRUD de usuarios
│       ├── trafico.astro      precisión de la IA, tiempos, gráficas, rutas
│       └── logs.astro         eventos, accesos y reporte CSV
└── styles/global.css     tema oscuro (Fig. 27-29), componentes y utilidades
```

## Decisiones

- **CSP estricta**: sin scripts en línea; Astro genera archivos JS externos
  (`inlineStylesheets: 'never'`, `assetsInlineLimit: 0`). Los scripts usan *top-level await*
  (`vite.build.target = es2022`).
- **Sin emojis**: los iconos son SVG de línea (`lib/iconos.ts`), heredan el color del texto.
- **Reautenticación transparente**: si la API responde 428, `api()` abre el modal de contraseña y
  repite la petición.
- **Tutorial obligatorio**: `requerirSesion('usuario')` redirige a `/app/tutorial` mientras
  `tutorial_visto` sea falso.

## Comandos

```bash
npm install
npm run dev       # http://localhost:4321 (proxy de /api a http://localhost:3000)
npm run build     # genera dist/ (lo hace la imagen "web" al construirse)
```
