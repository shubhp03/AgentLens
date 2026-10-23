/**
 * Server-Sent Events over fetch().
 *
 * Why not EventSource? It cannot send custom headers, and the gateway authenticates with X-API-Key.
 * The wire format is tiny, so we parse it ourselves: events are separated by a blank line and each
 * `data:` line contributes to the payload.
 */

/** Incremental SSE parser: feed it text chunks, get complete `data` payloads back. */
export class SseParser {
  private buf = '';

  push(chunk: string): string[] {
    this.buf += chunk.replace(/\r\n/g, '\n');
    const out: string[] = [];
    let idx: number;
    while ((idx = this.buf.indexOf('\n\n')) >= 0) {
      const block = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + 2);
      const data = block
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).replace(/^ /, ''))
        .join('\n');
      if (data) out.push(data);
    }
    return out;
  }
}

export interface SseOptions {
  url: string;
  headers?: Record<string, string>;
  signal: AbortSignal;
  onData: (data: string) => void;
}

/** Resolves when the server closes the stream; rejects on HTTP/network errors (aborts resolve quietly). */
export async function readSse({ url, headers, signal, onData }: SseOptions): Promise<void> {
  const res = await fetch(url, { headers: { Accept: 'text/event-stream', ...headers }, signal });
  if (!res.ok || !res.body) throw new Error(`event stream failed: HTTP ${res.status}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      for (const data of parser.push(value)) onData(data);
    }
  } catch (e) {
    if (signal.aborted) return;
    throw e;
  }
}
