import { describe, expect, it } from 'vitest';
import { SseParser } from './sse';

describe('SseParser', () => {
  it('emits one payload per blank-line-terminated event', () => {
    const p = new SseParser();
    expect(p.push('data: {"a":1}\n\ndata: {"a":2}\n\n')).toEqual(['{"a":1}', '{"a":2}']);
  });

  it('buffers events split across chunks', () => {
    const p = new SseParser();
    expect(p.push('data: {"kind":"span.fin')).toEqual([]);
    expect(p.push('ished"}\n')).toEqual([]);
    expect(p.push('\n')).toEqual(['{"kind":"span.finished"}']);
  });

  it('handles CRLF, comments, event/id fields and multi-line data', () => {
    const p = new SseParser();
    const out = p.push(': keep-alive\r\n\r\nid: 7\r\nevent: span\r\ndata: line1\r\ndata: line2\r\n\r\n');
    expect(out).toEqual(['line1\nline2']);
  });

  it('ignores events with no data', () => {
    expect(new SseParser().push('event: ping\n\n')).toEqual([]);
  });
});
