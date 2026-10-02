// 可控 FakeEventSource：jsdom 无原生 EventSource，测试经 vi.stubGlobal 注入。
export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  static reset() {
    FakeEventSource.instances = [];
  }

  url: string;
  closed = false;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, ((event: Event) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, callback: (event: Event) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(callback);
    this.listeners.set(type, list);
  }

  removeEventListener() {
    // 测试无需
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.(new Event('open'));
  }

  error() {
    this.onerror?.(new Event('error'));
  }

  emit(type: string, data: string, lastEventId = '') {
    const event = { data, lastEventId } as unknown as Event;
    this.listeners.get(type)?.forEach((callback) => callback(event));
  }
}
