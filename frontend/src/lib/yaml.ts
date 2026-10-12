/** A small YAML emitter for policy exports: JSON-shaped values in, block YAML out.
 *  Strings that are not plain identifiers are written JSON-quoted (valid YAML). */
const PLAIN = /^[A-Za-z_][A-Za-z0-9_.\-/]*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|y|n|~)$/i;

function scalar(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') return PLAIN.test(value) && !RESERVED.test(value) ? value : JSON.stringify(value);
  return String(value);
}

function isScalar(value: unknown): boolean {
  return value === null || typeof value !== 'object';
}

function isEmpty(value: unknown): boolean {
  return Array.isArray(value) ? value.length === 0 : typeof value === 'object' && value !== null && Object.keys(value).length === 0;
}

function emit(value: unknown, indent: number): string {
  const pad = '  '.repeat(indent);
  if (isScalar(value)) return scalar(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    return value.map((item) => `\n${pad}- ${isScalar(item) || isEmpty(item) ? emit(item, 0) : emit(item, indent + 1).trimStart()}`).join('');
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return '{}';
  return entries
    .map(([k, v]) => `\n${pad}${scalar(k)}:${isScalar(v) || isEmpty(v) ? ` ${emit(v, 0)}` : emit(v, indent + 1)}`)
    .join('');
}

export function toYaml(value: unknown): string {
  return `${emit(value, 0).replace(/^\n/, '')}\n`;
}
