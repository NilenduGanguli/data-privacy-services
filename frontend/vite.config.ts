/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist. `base: './'` keeps assets
// relative; nested console routes (/data-sources/jobs/<id>) request `<route>/assets/<file>`, which
// the app resolves by trying each path suffix inside dist (dps/app.py::_dist_file, M2 Task 24).
// The dev proxy follows the lane's app port (contract §0, lane-safe amendment 2026-10-12).
const appPort = process.env.DPS_APP_PORT ?? '8400';
const appUrl = `http://127.0.0.1:${appPort}`;

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2020',
    // The page stylesheets (src/styles/*.css) nest their rules under the page root; esbuild
    // flattens the nesting for browsers older than native CSS nesting.
    cssTarget: ['chrome105', 'edge105', 'firefox104', 'safari15'],
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: appUrl, changeOrigin: true },
      '/readyz': { target: appUrl, changeOrigin: true },
      '/health': { target: appUrl, changeOrigin: true },
      '/metrics': { target: appUrl, changeOrigin: true },
    },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
