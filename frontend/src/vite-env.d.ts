/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Dev only: `1` serves the UI-scanner routes from typed fixtures (src/dev/fixtureMode.ts). */
  readonly VITE_DPS_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
