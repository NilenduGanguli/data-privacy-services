/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

// The dev server proxies to this lane's app: lane-safe host port (contract §0, amendment
// 2026-10-12), default 8400.
const appPort = process.env.DPS_APP_PORT ?? '8400';
const appUrl = `http://127.0.0.1:${appPort}`;

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist at the site root. `base: '/'`
// makes asset URLs absolute (/assets/…), so nested routes such as /playground/documents,
// /policies/:name and /data-sources/jobs/:jobId load their bundles (M1 changed this from './').
// dps/app.py::_dist_file still resolves a suffix inside dist for a relative request (M2 Task 24).
// Hosting under a path prefix needs a gateway rewrite to the root.
// The sidebar footer shows the console version (M5 Task 18). Read from package.json so it is
// right however vite is started (npm scripts set npm_package_version, npx does not).
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as { version: string };

export default defineConfig({
  base: '/',
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? pkg.version ?? '0.0.0') },
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
  test: { environment: 'node', include: ['src/**/*.test.ts', 'src/**/*.test.tsx'] },
});
