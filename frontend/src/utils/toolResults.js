export function _parseToolResult(toolResult) {
  try {
    const parsed = typeof toolResult?.result === 'string'
      ? JSON.parse(toolResult.result)
      : toolResult?.result;
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

export function _isExportablePayload(parsed) {
  if (!parsed || typeof parsed !== 'object') return false;
  const hasItems = Array.isArray(parsed.items) && parsed.items.length > 0;
  const hasAnalysis = Array.isArray(parsed.analysis_data) && parsed.analysis_data.length > 0;
  const hasGroups = Array.isArray(parsed.groups) && parsed.groups.length > 0;
  return hasItems || hasAnalysis || hasGroups;
}

export function _parseExportIndex(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  if (!Number.isInteger(n)) return null;
  return n;
}

export function _toolResultHasPayload(toolResult) {
  const parsed = _parseToolResult(toolResult);
  if (_isExportablePayload(parsed)) return true;
  return !!String(toolResult?.result_blob_ref || '').trim();
}

export function getPreferredToolResult(toolResults, preferredIndex = null) {
  if (!Array.isArray(toolResults)) return null;
  const idx = _parseExportIndex(preferredIndex);
  if (idx !== null && idx >= 0 && idx < toolResults.length) {
    const preferred = toolResults[idx];
    if (_toolResultHasPayload(preferred)) return preferred;
  }
  for (let i = toolResults.length - 1; i >= 0; i--) {
    const tr = toolResults[i];
    if (_toolResultHasPayload(tr)) return tr;
  }
  return null;
}

export function getPreferredExportableData(toolResults, preferredIndex = null) {
  const selected = getPreferredToolResult(toolResults, preferredIndex);
  if (!selected) return null;
  return _parseToolResult(selected);
}

export function messageHasExportableData(message) {
  return !!getPreferredToolResult(message?.tool_results, message?.export_index);
}

export function getChartSpecs(toolResults) {
  if (!Array.isArray(toolResults)) return [];
  const charts = [];
  for (const tr of toolResults) {
    const parsed = _parseToolResult(tr);
    if (parsed && parsed._chart) {
      charts.push(parsed._chart);
    }
  }
  return charts;
}

export function getFileDownloads(toolResults) {
  if (!Array.isArray(toolResults)) return [];
  const downloads = [];
  for (const tr of toolResults) {
    const parsed = _parseToolResult(tr);
    if (parsed && parsed._file_download) {
      downloads.push(parsed._file_download);
    }
    if (parsed && Array.isArray(parsed._auto_file_downloads)) {
      downloads.push(...parsed._auto_file_downloads.filter(Boolean));
    }
  }
  return downloads.sort((a, b) => {
    const primaryA = a?.primary ? 1 : 0;
    const primaryB = b?.primary ? 1 : 0;
    return primaryB - primaryA;
  });
}

export function getPreferredAutoCsvDownload(toolResults, preferredIndex = null) {
  const selected = getPreferredToolResult(toolResults, preferredIndex);
  if (!selected) return null;
  const parsed = _parseToolResult(selected);
  if (!parsed) return null;
  const auto = Array.isArray(parsed._auto_file_downloads) ? parsed._auto_file_downloads : [];
  return auto.find((fd) => String(fd?.format || '').toLowerCase() === 'csv') || null;
}
