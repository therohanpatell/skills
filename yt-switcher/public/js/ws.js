/** WebSocket client with automatic exponential-backoff reconnect.
 *  All server state arrives here; the UI never polls. */

export function connectWs({ onMessage, onStatus }) {
  let delay = 500;
  let closedByUser = false;

  function open() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const sock = new WebSocket(`${proto}://${location.host}/ws`);

    sock.onopen = () => {
      delay = 500;
      onStatus('connected');
    };
    sock.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        onMessage(msg.type, msg.payload);
      } catch (_) { /* ignore malformed frames */ }
    };
    sock.onclose = () => {
      if (closedByUser) return;
      onStatus('reconnecting');
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 10000);
    };
    sock.onerror = () => sock.close();
  }

  open();
  return { close: () => { closedByUser = true; } };
}
