/**
 * Human-friendly rendering of span payloads.
 *
 * Tool outputs are stored as {"content": "<text>"} where the text is very often JSON itself. Showing that as an
 * escaped string inside JSON is unreadable, so we unwrap it and pretty-print the inner JSON.
 */
export function prettyPayload(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object' && Object.keys(value as object).length === 0) return '';

  let v: unknown = value;
  if (isRecord(v) && Object.keys(v).length === 1 && typeof v['content'] === 'string') {
    v = tryParseJson(v['content']) ?? v['content'];
  }
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function tryParseJson(text: string): unknown {
  const t = text.trim();
  if (!(t.startsWith('{') || t.startsWith('['))) return undefined;
  try {
    return JSON.parse(t);
  } catch {
    return undefined; // e.g. a truncated tool output: show it as plain text
  }
}
