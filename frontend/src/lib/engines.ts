/**
 * Per-engine rules of the New-job wizard (mockup 06; M3 Task 18b): the connection fields of
 * PostgreSQL, Oracle and MongoDB, owner/object labels, filter modes and the MongoDB filter's client
 * hints. Pure and value-free: nothing here keeps, logs or echoes a filter's text or a credential.
 * The server's selection validator (dps/layers/connectors/selection.py) stays authoritative.
 */
import type { ConnectionParams, WriteMode } from './types';

export type SourceEngine = 'postgres' | 'oracle' | 'mongo';

export interface FieldDef {
  key: 'host' | 'port' | 'database' | 'service_name' | `options.${string}`;
  label: string;
  required: boolean;
  placeholder?: string;
  defaultValue?: string | number;
  help?: string;
}

const HOST: FieldDef = { key: 'host', label: 'Host', required: true, placeholder: 'db.bank.internal' };

/** The fields of a new connection, in form order. */
export function connectionFields(engine: SourceEngine): FieldDef[] {
  switch (engine) {
    case 'oracle':
      return [
        { ...HOST, placeholder: 'crm-db.bank.internal' },
        { key: 'port', label: 'Port', required: true, defaultValue: 1521 },
        { key: 'service_name', label: 'Service name', required: true, placeholder: 'FREEPDB1', help: 'The PDB or service the listener serves, e.g. FREEPDB1.' },
      ];
    case 'mongo':
      return [
        { ...HOST, placeholder: 'mongo.bank.internal' },
        { key: 'port', label: 'Port', required: true, defaultValue: 27017 },
        { key: 'database', label: 'Database', required: true, placeholder: 'crm' },
        { key: 'options.replicaSet', label: 'Replica set', required: false, placeholder: 'rs0', help: 'In place and Append need a replica set (transactions).' },
        { key: 'options.directConnection', label: 'Direct connection', required: false, placeholder: 'true' },
        { key: 'options.authSource', label: 'Auth source', required: false, placeholder: 'admin' },
        { key: 'options.tls', label: 'TLS', required: false, placeholder: 'true' },
      ];
    case 'postgres':
    default:
      return [
        HOST,
        { key: 'port', label: 'Port', required: true, defaultValue: 5432 },
        { key: 'database', label: 'Database', required: true, placeholder: 'bank' },
        { key: 'options.sslmode', label: 'SSL mode', required: false, placeholder: 'require' },
      ];
  }
}

/** The default port of an engine (the form's starting value). */
export function defaultPort(engine: SourceEngine): string {
  return String(connectionFields(engine).find((f) => f.key === 'port')?.defaultValue ?? '');
}

/**
 * `ConnectionParams` from the form's values, or the first problem (value-free). With a secret
 * reference on Oracle or MongoDB (`secretRef`), the secret's `dsn` / `uri` file may name the server,
 * so host, port and the service name are optional there (the connection test tells).
 */
export function connectionParams(engine: SourceEngine, values: Record<string, string>, opts: { secretRef?: boolean } = {}): {
  params: ConnectionParams; problem: string | null;
} {
  const params: ConnectionParams = { options: {} };
  let problem: string | null = null;
  for (const f of connectionFields(engine)) {
    const raw = (values[f.key] ?? '').trim();
    const addressed = engine !== 'postgres' && !!opts.secretRef && (f.key === 'host' || f.key === 'port' || f.key === 'service_name');
    const optional = !f.required || addressed;
    if (!raw) {
      if (!optional && !problem) problem = `${f.label} is required.`;
      continue;
    }
    if (f.key === 'port') {
      const port = Number(raw);
      if (!Number.isInteger(port) || port < 1 || port > 65535) { problem = problem ?? 'Port must be a number from 1 to 65535.'; continue; }
      params.port = port;
    } else if (f.key.startsWith('options.')) {
      params.options![f.key.slice('options.'.length)] = raw;
    } else {
      (params as Record<string, unknown>)[f.key] = raw;
    }
  }
  return { params, problem };
}

/** What the files of a secret reference hold, per engine. */
export function secretRefHelp(engine: SourceEngine): string {
  switch (engine) {
    case 'oracle':
      return 'The secret holds the files username and password, and optionally dsn (it overrides host, port and service name). The job stores only this name.';
    case 'mongo':
      return 'The secret holds an optional uri file (it overrides host and port) or the files username and password. The job stores only this name.';
    case 'postgres':
    default:
      return 'The job stores only this name. Workers read the secret from the mounted store when they start; it never appears in the console, the logs or the evidence pack.';
  }
}

/** "Owner" (a PostgreSQL schema or an Oracle owner); MongoDB has none (the database is the connection's). */
export function ownerLabel(engine: SourceEngine): string | null {
  return engine === 'mongo' ? null : 'Owner';
}

export function objectLabel(engine: SourceEngine): 'Table' | 'Collection' {
  return engine === 'mongo' ? 'Collection' : 'Table';
}

/** The selection modes of step 2: SQL for PostgreSQL and Oracle, a JSON filter for MongoDB. */
export function filterModes(engine: SourceEngine): ('where' | 'query' | 'mongo')[] {
  return engine === 'mongo' ? ['mongo'] : ['where', 'query'];
}

const ENGINE_TITLE: Record<SourceEngine, string> = { postgres: 'PostgreSQL', oracle: 'Oracle Database', mongo: 'MongoDB' };

/** "Oracle Database 23.26.0.0" from a connection test's server version. */
export function versionLabel(engine: string, version: string | null | undefined): string {
  const title = ENGINE_TITLE[engine as SourceEngine] ?? engine;
  return version ? `${title} ${version}` : title;
}

/** Operators that run JavaScript on the server: refused by DPS (a client hint; the server decides). */
const JS_OPERATORS = ['$where', '$expr', '$function', '$accumulator'];

function findOperator(node: unknown, depth = 0): string | null {
  if (depth > 64 || node === null || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const item of node) { const hit = findOperator(item, depth + 1); if (hit) return hit; }
    return null;
  }
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    if (JS_OPERATORS.includes(k)) return k;
    const hit = findOperator(v, depth + 1);
    if (hit) return hit;
  }
  return null;
}

/**
 * Parse the step 2 filter: a JSON object, or empty (every document). Errors are fixed sentences
 * (an operator name at most) and never contain the text: a filter holds customer values.
 */
export function parseMongoFilter(text: string): { filter: Record<string, unknown> | null; error: string | null } {
  if (!text.trim()) return { filter: null, error: null };
  let doc: unknown;
  try { doc = JSON.parse(text); } catch { return { filter: null, error: 'The filter is not valid JSON.' }; }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    return { filter: null, error: 'The filter must be a JSON object, such as {"segment": "premium"}.' };
  }
  const op = findOperator(doc);
  if (op) return { filter: null, error: `${op} runs JavaScript on the server and is not allowed in a DPS filter.` };
  return { filter: doc as Record<string, unknown>, error: null };
}

/** The filter's shape with every literal replaced by "<bound>" (the rail shows this, like SQL binds). */
export function mongoFilterTemplate(filter: Record<string, unknown>): Record<string, unknown> {
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(walk);
    if (v !== null && typeof v === 'object') return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, walk(x)]));
    return '<bound>';
  };
  return walk(filter) as Record<string, unknown>;
}

/** In place and Append write with the ledger in one transaction: on MongoDB that needs a replica set. */
export function needsTransactions(mode: WriteMode | null | undefined): boolean {
  return mode === 'in_place' || mode === 'append';
}

/**
 * How the review shows a column: a document path without `$.` (`profile.fullName`,
 * `accounts[].iban`), a SQL column (or a top-level field) by its name.
 */
export function columnLabel(c: { path: string; name: string }): string {
  const p = c.path.replace(/^\$\.?/, '').replace(/\[\*\]/g, '[]');
  return p && p !== c.name ? p : c.name;
}
