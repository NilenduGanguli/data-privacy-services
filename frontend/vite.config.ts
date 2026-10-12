/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies to this lane's app: lane-safe host port (contract §0, amendment
// 2026-10-12), default 8400.
const appPort = process.env.DPS_APP_PORT ?? '8400';
const appUrl = `http://127.0.0.1:${appPort}`;

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist at the site root. `base: '/'`
// makes asset URLs absolute (/assets/…), so nested routes such as /playground/documents,
// /policies/:name and /data-sources/jobs/:jobId load their bundles (M1 changed this from './').
// dps/app.py::_dist_file still resolves a suffix inside dist for a relative request (M2 Task 24).
// Hosting under a path prefix needs a gateway rewrite to the root.
export default defineConfig({
  base: '/',
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
