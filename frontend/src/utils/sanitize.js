import DOMPurify from 'dompurify';

export function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function sanitizeLinkUrl(rawUrl) {
  const candidate = String(rawUrl || '').trim();
  if (!candidate) return '';
  try {
    const parsed = new URL(candidate, window.location.origin);
    const protocol = (parsed.protocol || '').toLowerCase();
    if (protocol === 'http:' || protocol === 'https:') {
      return parsed.href;
    }
  } catch (_) {
    return '';
  }
  return '';
}

export function sanitizeHtmlOutput(rawHtml) {
  return DOMPurify.sanitize(String(rawHtml || ''), {
    ALLOWED_TAGS: ['a', 'b', 'i', 'em', 'strong', 'p', 'ul', 'ol', 'li', 'br', 'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span'],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class', 'title'],
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['onerror', 'onclick', 'onload'],
  });
}
