import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies to this lane's app: lane-safe host port (contract §0), default 8400.
const API = `http://localhost:${process.env.DPS_APP_PORT || '8400'}`;

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist at the site root. `base: '/'`
// makes asset URLs absolute (/assets/…), so nested routes such as /playground/documents and
// /policies/:name load their bundles (M1 changed this from './', which only worked one segment deep).
// Hosting under a path prefix needs a gateway rewrite to the root.
export default defineConfig({
  base: '/',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2020' },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/readyz': { target: API, changeOrigin: true },
      '/health': { target: API, changeOrigin: true },
      '/metrics': { target: API, changeOrigin: true },
    },
  },
});
