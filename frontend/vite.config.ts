import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist. `base: './'` keeps assets
// relative, so every route must be ONE segment deep (/playground, never /jobs/123) — see the
// sibling console's vite.config.ts for the full explanation.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2020' },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8400', changeOrigin: true },
      '/readyz': { target: 'http://localhost:8400', changeOrigin: true },
      '/health': { target: 'http://localhost:8400', changeOrigin: true },
      '/metrics': { target: 'http://localhost:8400', changeOrigin: true },
    },
  },
});
