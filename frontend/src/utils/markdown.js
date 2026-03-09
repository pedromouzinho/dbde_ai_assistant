import { escapeHtml, sanitizeHtmlOutput, sanitizeLinkUrl } from './sanitize.js';

function renderInlineMarkdown(rawText) {
  let text = String(rawText || '');
  const linkPlaceholders = [];

  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    const safeHref = sanitizeLinkUrl(url);
    if (!safeHref) return escapeHtml(label);
    const token = `@@LINK_${linkPlaceholders.length}@@`;
    linkPlaceholders.push(
      `<a href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
    );
    return token;
  });

  let html = escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])((?:https?:\/\/|\/api\/)[^\s<)]+)/g, (_, prefix, url) => {
      const safeHref = sanitizeLinkUrl(url);
      if (!safeHref) return `${prefix}${url}`;
      return `${prefix}<a href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`;
    });

  for (let i = 0; i < linkPlaceholders.length; i++) {
    html = html.replaceAll(escapeHtml(`@@LINK_${i}@@`), linkPlaceholders[i]);
  }
  return html;
}

function isTableSeparatorLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed.includes('-')) return false;
  const core = trimmed.replace(/^\|/, '').replace(/\|$/, '');
  return core
    .split('|')
    .map(cell => cell.trim())
    .every(cell => /^:?-{2,}:?$/.test(cell));
}

function isTableRowLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed || !trimmed.includes('|')) return false;
  return !isTableSeparatorLine(trimmed);
}

function parseTableRow(line) {
  const trimmed = String(line || '').trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map(cell => cell.trim());
}

function renderTableBlock(headerRow, dataRows) {
  const colCount = Math.max(1, headerRow.length);
  const normalizedHeader = [...headerRow];
  while (normalizedHeader.length < colCount) normalizedHeader.push('');

  const headerHtml = normalizedHeader
    .slice(0, colCount)
    .map(cell => `<th>${renderInlineMarkdown(cell)}</th>`)
    .join('');

  const bodyHtml = dataRows
    .map((row) => {
      const normalized = [...row];
      while (normalized.length < colCount) normalized.push('');
      return `<tr>${normalized.slice(0, colCount).map(cell => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`;
    })
    .join('');

  return `<div class="table-wrapper"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function renderRegularBlock(lines) {
  return lines.map(line => renderInlineMarkdown(line)).join('<br>');
}

export function renderMarkdown(text) {
  const source = String(text || '').replace(/\r\n?/g, '\n');
  const lines = source.split('\n');
  const segments = [];
  let regularBuffer = [];
  let i = 0;

  function flushRegular() {
    if (regularBuffer.length === 0) return;
    segments.push(renderRegularBlock(regularBuffer));
    regularBuffer = [];
  }

  while (i < lines.length) {
    const line = lines[i];
    const next = i + 1 < lines.length ? lines[i + 1] : '';
    const tableStart = isTableRowLine(line) && isTableSeparatorLine(next);

    if (!tableStart) {
      regularBuffer.push(line);
      i += 1;
      continue;
    }

    flushRegular();
    const headerRow = parseTableRow(line);
    const dataRows = [];
    i += 2; // skip header + separator

    while (i < lines.length && isTableRowLine(lines[i])) {
      dataRows.push(parseTableRow(lines[i]));
      i += 1;
    }

    segments.push(renderTableBlock(headerRow, dataRows));
  }

  flushRegular();
  return sanitizeHtmlOutput(segments.join('<br>'));
}

export function renderInline(text) {
  return sanitizeHtmlOutput(renderInlineMarkdown(text));
}
