export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = formatApiErrorDetail(payload.detail ?? payload.message ?? detail);
    } catch {
      // Keep the HTTP status text.
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail.map(formatValidationError).join('\n');
  }

  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }

  return String(detail);
}

function formatValidationError(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return String(error);
  }

  const item = error as { loc?: unknown; msg?: unknown };
  const location = Array.isArray(item.loc)
    ? item.loc.filter((part) => part !== 'body').join('.')
    : '';
  const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(error);

  return location ? `${location}: ${message}` : message;
}
