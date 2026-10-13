/// <reference types="vite/client" />

/** The console version from package.json (vite.config.ts `define`). */
declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  /** `1` in the dev server's fixture mode (`npm run dev:fixtures`); never set for a build. */
  readonly VITE_DPS_FIXTURES?: string;
}
