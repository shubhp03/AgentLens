/** Small, pure formatters used by pipes and components. */

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

export function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  if (n === 0) return '$0';
  if (Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  if (Math.abs(n) < 10) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

export function fmtPct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  return `${(x * 100).toFixed(digits)}%`;
}

/** "62.5% (48.1–74.9)" – a point estimate with its Wilson interval, as the eval design requires. */
export function fmtPctCi(x: number | null | undefined, ci: number[] | null | undefined): string {
  if (x === null || x === undefined) return '–';
  if (!ci || ci.length !== 2) return fmtPct(x);
  return `${fmtPct(x)} (${(ci[0] * 100).toFixed(0)}–${(ci[1] * 100).toFixed(0)})`;
}

export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  return n.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : '–';
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '–';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' });
}

/** Formats a p-value without printing a misleading "0.000". */
export function fmtP(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return '–';
  return p < 0.001 ? '< 0.001' : p.toFixed(3);
}
