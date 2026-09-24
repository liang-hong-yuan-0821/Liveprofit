// Event display only. No DOM/HTML parser and no recursive entity expansion.
export function normalizeEventText(text: string) {
  const entities: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', nbsp: ' ' };
  return text.replace(/<\/?(b|strong|em|i|p|div|br|span)\b[^>]*>/gi, (tag: string) => /^<\/?(?:p|div|br)\b/i.test(tag) ? '\n' : '')
    .replace(/&(amp|lt|gt|quot|nbsp);/gi, (_, name: string) => entities[name.toLowerCase()])
    .replace(/\n{3,}/g, '\n\n').trim();
}
