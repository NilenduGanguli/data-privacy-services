/**
 * DEV ONLY — fixture mode for the UI-scanner pages. `VITE_DPS_FIXTURES=1 npm run dev` makes main.tsx
 * import this module (behind `import.meta.env.DEV`, so the production bundle never contains it) and
 * install a `fetch` wrapper: the scans and artefacts routes are answered by ScanFixtureServer, every
 * other request goes to the real API through the dev proxy. Screenshots are painted on a canvas from
 * the fixture fields (synthetic values only).
 */
import type { ScanField, ScanPage } from '../lib/types';
import { FIXTURE_MARKER, ScanFixtureServer, type ArtefactInfo } from './scanFixtureServer';

const INK = '#141a2b';
const MUTED = '#7e89a6';
const BORDER = '#c5cde0';
const BOX: Record<string, string> = { annotated: '#d97706', filled_synthesize: '#7c3aed', filled_obfuscate: '#0f766e', masked_capture: '#0f766e', original: '#9aa3b8' };

const RENDERED_TOKENS: Record<string, [string, string]> = {
  PERSON_NAME: ['Full name', 'Tilqoe Mzvhrpkat'], DATE_OF_BIRTH: ['Date of birth', '02/11/1987'], CUSTOMER_ID: ['Customer ID', 'CUST-730214'],
  AADHAAR: ['Aadhaar', '8036 1194 7721'], PAN_IN: ['PAN number', 'XKDPQ8810T'], ACCOUNT_NUMBER: ['Account number', '000977104238'],
  CARD_PAN: ['Card number', '4000 0098 1276 3341'], EMAIL: ['Email', 'vqzmra.p@example.com'], PHONE: ['Mobile', '+91 93117 84062'],
  STREET_ADDRESS: ['Address', '[[PII:STREET_ADDRESS:m4q7t2…]]'],
};

function badge(ctx: CanvasRenderingContext2D, n: number, x: number, y: number, color: string): void {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, 10, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(String(n), x, y + 4);
  ctx.textAlign = 'left';
}

function valueFor(kind: string, f: ScanField): string {
  if (kind === 'filled_synthesize') return f.fill_synthesize ?? (f.input_type === 'password' ? '••••••••' : '');
  if (kind === 'filled_obfuscate') return f.fill_obfuscate ?? (f.input_type === 'password' ? '••••••••' : '');
  return ''; // the original and annotated screens are captured before filling (pre-filled values are masked)
}

function paintForm(ctx: CanvasRenderingContext2D, kind: string, fields: ScanField[], width: number): void {
  const color = BOX[kind] ?? BOX.original;
  fields.forEach((f, i) => {
    const col = f.tag === 'textarea' ? 0 : i % 2;
    const x = 32 + col * ((width - 80) / 2 + 16);
    const y = 96 + Math.floor(i / 2) * 64;
    const w = f.tag === 'textarea' ? width - 64 : (width - 80) / 2;
    ctx.fillStyle = MUTED;
    ctx.font = '12px system-ui, sans-serif';
    ctx.fillText(f.label ?? `field ${f.field_no}`, x, y);
    ctx.fillStyle = f.prefilled && kind === 'original' ? '#e5e7eb' : '#ffffff';
    ctx.strokeStyle = kind === 'original' ? BORDER : color;
    ctx.lineWidth = kind === 'original' ? 1 : 2;
    ctx.fillRect(x, y + 8, w, 34);
    ctx.strokeRect(x, y + 8, w, 34);
    const v = valueFor(kind, f);
    if (v) {
      ctx.fillStyle = INK;
      ctx.font = '13px ui-monospace, Menlo, monospace';
      ctx.fillText(v.length > 40 ? `${v.slice(0, 39)}…` : v, x + 10, y + 30);
    }
    if (kind !== 'original') badge(ctx, f.field_no, x + w - 4, y + 8, color);
  });
  const rows = Math.ceil(fields.length / 2);
  ctx.fillStyle = '#e5e7eb';
  ctx.fillRect(width - 160, 96 + rows * 64, 128, 36);
  ctx.fillStyle = MUTED;
  ctx.font = 'bold 13px system-ui, sans-serif';
  ctx.fillText('Continue →', width - 136, 96 + rows * 64 + 23);
}

function paintRendered(ctx: CanvasRenderingContext2D, kind: string, page: ScanPage, width: number): void {
  const entities = Object.keys(page.rendered_findings);
  entities.forEach((entity, i) => {
    const [label, token] = RENDERED_TOKENS[entity] ?? [entity, '[[PII]]'];
    const y = 100 + i * 38;
    ctx.fillStyle = MUTED;
    ctx.font = '13px system-ui, sans-serif';
    ctx.fillText(label, 32, y);
    if (kind === 'masked_capture') {
      ctx.fillStyle = 'rgba(15,118,110,.12)';
      ctx.fillRect(200, y - 16, Math.min(width - 240, token.length * 8 + 16), 24);
      ctx.fillStyle = BOX.masked_capture;
      ctx.font = '13px ui-monospace, Menlo, monospace';
      ctx.fillText(token, 208, y);
    } else {
      // the original screenshot of a page that renders personal data is stored with boxes over it
      ctx.fillStyle = '#4b5563';
      ctx.fillRect(200, y - 16, 180, 24);
    }
  });
}

/** Paint a screenshot artefact of a fixture page (900 px wide, like the worker's viewport ratio). */
export async function paint(info: ArtefactInfo): Promise<Blob> {
  const width = 900;
  const rows = info.fields.length ? Math.ceil(info.fields.length / 2) * 64 + 160 : Object.keys(info.page?.rendered_findings ?? {}).length * 38 + 140;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = Math.max(260, rows);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('no 2d context');
  ctx.fillStyle = '#f7f8fb';
  ctx.fillRect(0, 0, width, canvas.height);
  ctx.fillStyle = '#0f766e';
  ctx.fillRect(0, 0, width, 44);
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 15px system-ui, sans-serif';
  ctx.fillText('Demo Bank · onboarding', 24, 28);
  ctx.fillStyle = INK;
  ctx.font = 'bold 20px system-ui, sans-serif';
  ctx.fillText(info.page?.title_redacted ?? 'Page', 32, 76);
  if (info.fields.length) paintForm(ctx, info.kind, info.fields, width);
  else if (info.page) paintRendered(ctx, info.kind, info.page, width);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png'));
}

/** Serve the scanner routes from fixtures in this tab (dev server only). */
export function installFixtureMode(): void {
  const server = new ScanFixtureServer({ png: paint });
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? (input instanceof Request ? input.method : 'GET');
    const body = typeof init?.body === 'string' ? init.body : undefined;
    const answer = await server.handle(method, url, body);
    return answer ?? realFetch(input, init);
  };
  document.documentElement.dataset.fixtures = [document.documentElement.dataset.fixtures, 'scanner'].filter(Boolean).join(' ');
  const tag = Object.assign(document.createElement('div'), { textContent: 'fixtures', title: 'UI-scanner routes are served from dev fixtures' });
  tag.setAttribute('style', 'position:fixed;right:10px;bottom:10px;z-index:50;font:700 10.5px system-ui;letter-spacing:.06em;text-transform:uppercase;padding:3px 8px;border-radius:99px;background:var(--amber-soft);color:var(--amber);border:1px solid var(--amber-border);pointer-events:none');
  document.body.appendChild(tag);
  console.info(`[${FIXTURE_MARKER}] UI-scanner routes are served from typed fixtures`);
}
