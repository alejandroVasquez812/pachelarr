// Tiny client-side ring buffer used by the latency sparkline to keep the
// last N samples across SWR polls without unbounded growth.

export class RingBuffer<T> {
  private buf: T[] = [];
  private maxLen: number;
  private head = 0;
  private size = 0;

  constructor(maxLen: number) {
    this.maxLen = maxLen;
  }

  push(v: T): void {
    this.buf[this.head] = v;
    this.head = (this.head + 1) % this.maxLen;
    if (this.size < this.maxLen) {
      this.size++;
    }
  }

  toArray(): T[] {
    const out: T[] = [];
    const start = this.size < this.maxLen ? 0 : this.head;
    for (let i = 0; i < this.size; i++) {
      out.push(this.buf[(start + i) % this.maxLen]);
    }
    return out;
  }

  clear(): void {
    this.buf = [];
    this.head = 0;
    this.size = 0;
  }
}
