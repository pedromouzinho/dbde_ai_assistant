export function getAuthHeaders() {
  return { 'Content-Type': 'application/json' };
}

export function authFetch(url, options = {}) {
  return fetch(url, { credentials: 'include', ...options });
}
