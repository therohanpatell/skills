/** Thin REST client. Every mutation returns the server's JSON or throws
 *  an Error whose message is safe to show the operator. */

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = {};
  try {
    json = await res.json();
  } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(json.error || `Request failed (${res.status})`);
  return json;
}

export const api = {
  addSource: (url) => request('POST', '/api/sources', { url }),
  removeSource: (id) => request('DELETE', `/api/sources/${id}`),
  clearSources: () => request('DELETE', '/api/sources'),
  refreshSource: (id) => request('POST', `/api/sources/${id}/refresh`),
  setProgram: (id) => request('POST', `/api/program/${id}`),
  stopProgram: () => request('DELETE', '/api/program'),
  playback: (action, value) => request('POST', '/api/playback', { action, value }),
  toggleMonitor: () => request('POST', '/api/monitor/toggle'),
  resetVcam: () => request('POST', '/api/vcam/reset'),
  getState: () => request('GET', '/api/state'),
};
