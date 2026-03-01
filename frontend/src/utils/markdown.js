import { escapeHtml, sanitizeHtmlOutput } from './sanitize.js';

export function renderMarkdown(text) {
  const html = escapeHtml(text || '')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
  return sanitizeHtmlOutput(html);
}

export function renderInline(text) {
  return sanitizeHtmlOutput(escapeHtml(text || ''));
}
