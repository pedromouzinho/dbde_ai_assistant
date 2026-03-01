export async function exportConversation(format, payload, authHeaders = {}) {
  const response = await fetch('/api/export-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: JSON.stringify({ format, ...payload }),
  });
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }
  return await response.json();
}
