// Talks only to the gateway's /api/v1 on the same origin (the Ingress routes /api to the gateway).
// The member's API key is kept in sessionStorage: per browser tab, cleared when the tab closes.

const STORAGE_KEY = 'urumi.gatewayKey';

export function loadKey() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function storeKey(key) {
  try {
    if (key) sessionStorage.setItem(STORAGE_KEY, key);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable (private mode); the session then lasts until reload.
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function describe(detail, status) {
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [..., "field"], msg: "..."}]
    return detail.map((item) => `${item.loc?.[item.loc.length - 1] ?? 'input'}: ${item.msg}`).join('; ');
  }
  return detail || `Request failed (${status})`;
}

export async function apiRequest(key, path, { method = 'GET', body } = {}) {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${key}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, describe(data.detail, response.status));
  return data;
}

export const gatewayMcpUrl = () => `${window.location.origin}/mcp`;
