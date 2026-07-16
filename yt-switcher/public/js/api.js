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

  // DVR Controls
  seekForward: (secs = 10) => request('POST', '/api/playback', { action: 'seek-forward', value: secs }),
  seekBackward: (secs = 10) => request('POST', '/api/playback', { action: 'seek-backward', value: secs }),
  seekTo: (seconds) => request('POST', '/api/playback', { action: 'seek-to', value: seconds }),
  seekPercent: (pct) => request('POST', '/api/playback', { action: 'seek-percent', value: pct }),

  // Audio device & Speed
  setAudioDevice: (device) => request('POST', '/api/playback', { action: 'audio-device', value: device }),
  setVcamDevice: (device) => request('POST', '/api/playback', { action: 'vcam-device', value: device }),
  setSpeed: (speed) => request('POST', '/api/playback', { action: 'speed', value: speed }),
};
