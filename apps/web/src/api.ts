export class APIError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...init,
      headers: { ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    });
  } catch {
    throw new APIError('The local API is unreachable. Check that Docker Compose is running.', 0);
  }
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { const data = await response.json(); message = data.error?.message || message; } catch { /* Proxy errors may not be JSON. */ }
    throw new APIError(message, response.status);
  }
  return response.json() as Promise<T>;
}
export async function exportDownload(runId: string, format: 'json' | 'markdown'): Promise<void> {
  const response = await fetch(`/api/v1/runs/${runId}/export?format=${format}`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new APIError(data.error?.message || 'Export could not be created.', response.status);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = `kdaa-run-${runId}.${format === 'json' ? 'json' : 'md'}`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
