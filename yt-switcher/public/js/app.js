/** UI orchestrator: renders server state pushed over the WebSocket and maps
 *  user actions to REST calls. DOM nodes for cards are reused across renders
 *  (keyed by source id) so thumbnails never flicker or re-download. */

import { api } from './api.js';
import { connectWs } from './ws.js';

const $ = (id) => document.getElementById(id);

const els = {
  grid: $('previewGrid'),
  cardTpl: $('cardTemplate'),
  sourceCount: $('sourceCount'),
  programPreview: $('programPreview'),
  programOverlay: $('programOverlay'),
  overlayText: $('overlayText'),
  programTally: $('programTally'),
  programSourceTitle: $('programSourceTitle'),
  infoRes: $('infoRes'),
  infoFps: $('infoFps'),
  infoBuffer: $('infoBuffer'),
  infoLive: $('infoLive'),
  infoTime: $('infoTime'),
  addForm: $('addForm'),
  urlInput: $('urlInput'),
  btnAdd: $('btnAdd'),
  addError: $('addError'),
  vcamFormat: $('vcamFormat'),
  vcamStatus: $('vcamStatus'),
  monitorStatus: $('monitorStatus'),
  volume: $('volume'),
  volumeLabel: $('volumeLabel'),
  btnMute: $('btnMute'),
  selectedInfo: $('selectedInfo'),
  connState: $('connState'),
  procTable: $('procTable'),
};

let state = null;
const cardNodes = new Map(); // source id -> element
let previewAttached = false;

/* ------------------------------ rendering ------------------------------ */

function render(newState) {
  state = newState;
  renderGrid();
  renderProgram();
  renderSidePanel();
}

function renderGrid() {
  const seen = new Set();
  let index = 0;
  for (const source of state.sources) {
    seen.add(source.id);
    let card = cardNodes.get(source.id);
    if (!card) {
      card = createCard(source.id);
      cardNodes.set(source.id, card);
    }
    updateCard(card, source);
    // Keep DOM order in sync without re-appending unchanged positions.
    if (els.grid.children[index] !== card) {
      els.grid.insertBefore(card, els.grid.children[index] || null);
    }
    index++;
  }
  for (const [id, node] of cardNodes) {
    if (!seen.has(id)) {
      node.remove();
      cardNodes.delete(id);
    }
  }
  els.sourceCount.textContent = `${state.sources.length} / ${state.limits.maxSources}`;
}

function createCard(id) {
  const card = els.cardTpl.content.firstElementChild.cloneNode(true);
  card.dataset.id = id;
  card.addEventListener('click', (ev) => {
    if (ev.target.classList.contains('card-remove')) return;
    api.setProgram(id).catch(showError);
  });
  card.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      api.setProgram(id).catch(showError);
    }
  });
  card.querySelector('.card-remove').addEventListener('click', () => {
    api.removeSource(id).catch(showError);
  });
  return card;
}

function updateCard(card, source) {
  const img = card.querySelector('.thumb img');
  const thumbUrl = source.thumbnail || '';
  if (img.getAttribute('src') !== thumbUrl) img.setAttribute('src', thumbUrl);

  setText(card.querySelector('.card-title'), source.title);
  setText(card.querySelector('.card-sub'), source.channel || hostOf(source.url));

  card.classList.toggle('is-live', Boolean(source.isLive));
  card.classList.toggle('is-loading', source.status === 'resolving');
  card.classList.toggle('error-state', source.status === 'error');
  card.classList.toggle('program', state.program.programId === source.id);

  const errBadge = card.querySelector('.error-badge');
  if (source.status === 'error' && source.error) {
    setText(errBadge, source.error.message);
    errBadge.title = source.error.message;
  } else {
    setText(errBadge, '');
  }
}

function renderProgram() {
  const { programId, status, error } = state.program;
  const source = state.sources.find((s) => s.id === programId);
  const active = status === 'playing' || status === 'starting';

  els.programTally.classList.toggle('on', status === 'playing');
  setText(els.programSourceTitle, source ? `— ${source.title}` : '');

  // Only hold the MJPEG connection open while a program is actually running.
  if (active && !previewAttached) {
    els.programPreview.src = '/api/preview.mjpeg?t=' + Date.now();
    els.programPreview.classList.add('visible');
    previewAttached = true;
  } else if (!active && previewAttached) {
    els.programPreview.src = '';
    els.programPreview.classList.remove('visible');
    previewAttached = false;
  }

  const overlayMsgs = {
    idle: 'NO PROGRAM — select a source below',
    resolving: 'RESOLVING STREAM…',
    starting: 'CONNECTING…',
    error: error ? `ERROR — ${error.message}` : 'ERROR',
  };
  if (status === 'playing') {
    els.programOverlay.classList.add('hidden');
  } else {
    els.programOverlay.classList.remove('hidden');
    setText(els.overlayText, overlayMsgs[status] || status);
    els.overlayText.classList.toggle('error', status === 'error');
  }

  setText(els.infoLive, source ? (source.isLive ? 'LIVE' : 'VOD') : '—');
  els.infoLive.classList.toggle('live', Boolean(source && source.isLive));
}

function renderSidePanel() {
  const v = state.vcam;
  setText(els.vcamFormat, `${v.width}×${v.height} @ ${v.fps}fps`);
  setPill(els.vcamStatus, v.status);
  setPill(els.monitorStatus, state.monitor.status);

  els.volume.value = state.settings.volume;
  setText(els.volumeLabel, String(state.settings.volume));
  els.btnMute.classList.toggle('active', state.settings.muted);

  const source = state.sources.find((s) => s.id === state.program.programId);
  if (!source) {
    els.selectedInfo.innerHTML = '<span class="dim">Nothing selected</span>';
  } else {
    els.selectedInfo.innerHTML = '';
    for (const [k, val] of [
      ['Title', source.title],
      ['Channel', source.channel || '—'],
      ['Type', source.isLive ? 'Live stream' : 'Video'],
      ['Duration', source.isLive ? '∞' : fmtTime(source.duration)],
      ['URL', source.url],
    ]) {
      const row = document.createElement('div');
      row.className = 'kv';
      const kEl = document.createElement('span');
      kEl.textContent = k;
      const vEl = document.createElement('b');
      vEl.textContent = val;
      vEl.title = String(val);
      row.append(kEl, vEl);
      els.selectedInfo.appendChild(row);
    }
  }
}

function renderStats(stats) {
  setText($('statCpu'), `${stats.appCpu}%`);
  setText($('statRam'), `${stats.appRamMb} MB`);
  setText($('statSysRam'), `${(stats.system.ramUsedMb / 1024).toFixed(1)} / ${(stats.system.ramTotalMb / 1024).toFixed(1)} GB`);
  setText($('statGpu'), stats.gpu ? `${stats.gpu.utilization}%` : 'n/a');
  setText($('statGpuDec'), stats.gpu ? `${stats.gpu.decoder}%` : 'n/a');

  els.procTable.innerHTML = '';
  for (const [label, p] of Object.entries(stats.processes)) {
    const row = document.createElement('div');
    const nameEl = document.createElement('span');
    nameEl.textContent = label;
    const valEl = document.createElement('span');
    valEl.textContent = `${p.cpu}%  ${p.ramMb} MB`;
    row.append(nameEl, valEl);
    els.procTable.appendChild(row);
  }
}

function renderMpvProps(props) {
  const vp = props['video-params'];
  setText(els.infoRes, vp && vp.w ? `${vp.w}×${vp.h}` : '—');
  const fps = props['estimated-vf-fps'];
  setText(els.infoFps, fps ? `${fps.toFixed(1)} fps` : '— fps');
  const cache = props['demuxer-cache-duration'];
  setText(els.infoBuffer, cache != null ? `buf ${cache.toFixed(1)}s` : 'buf —');
  setText(els.infoTime, fmtTime(props['time-pos']));
}

/* ------------------------------- helpers ------------------------------- */

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text ?? '';
}

function setPill(el, status) {
  setText(el, status);
  el.className = 'status-pill ' + (
    status === 'running' ? 'ok'
    : status === 'failed' ? 'err'
    : status === 'disabled' || status === 'stopped' ? ''
    : 'warn'
  );
}

function fmtTime(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function hostOf(url) {
  try { return new URL(url).host; } catch (_) { return ''; }
}

function showError(err) {
  setText(els.addError, err.message);
  clearTimeout(showError._t);
  showError._t = setTimeout(() => setText(els.addError, ''), 6000);
}

/* ------------------------------- actions ------------------------------- */

els.addForm.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const url = els.urlInput.value.trim();
  if (!url) return;
  els.btnAdd.disabled = true;
  setText(els.addError, '');
  try {
    await api.addSource(url);
    els.urlInput.value = '';
  } catch (err) {
    showError(err);
  } finally {
    els.btnAdd.disabled = false;
  }
});

$('btnClearAll').addEventListener('click', () => {
  if (state && state.sources.length && confirm('Remove ALL sources?')) {
    api.clearSources().catch(showError);
  }
});

$('btnPlay').addEventListener('click', () => api.playback('play').catch(showError));
$('btnPause').addEventListener('click', () => api.playback('pause').catch(showError));
$('btnStop').addEventListener('click', () => api.stopProgram().catch(showError));
$('btnMute').addEventListener('click', () => {
  api.playback(state && state.settings.muted ? 'unmute' : 'mute').catch(showError);
});
els.volume.addEventListener('input', () => setText(els.volumeLabel, els.volume.value));
els.volume.addEventListener('change', () => {
  api.playback('volume', Number(els.volume.value)).catch(showError);
});
$('btnFullscreen').addEventListener('click', () => {
  const screen = $('programScreen');
  if (document.fullscreenElement) document.exitFullscreen();
  else screen.requestFullscreen().catch(() => {});
});
$('btnMonitorToggle').addEventListener('click', () => api.toggleMonitor().catch(showError));
$('btnVcamReset').addEventListener('click', () => api.resetVcam().catch(showError));

/* ------------------------------- startup ------------------------------- */

connectWs({
  onMessage(type, payload) {
    if (type === 'state') render(payload);
    else if (type === 'stats') renderStats(payload);
    else if (type === 'mpvProps') renderMpvProps(payload);
  },
  onStatus(status) {
    setText(els.connState, status === 'connected' ? '● connected' : '○ reconnecting…');
    els.connState.className = 'conn ' + (status === 'connected' ? 'ok' : 'err');
    // After a reconnect the push stream resumes; fetch a snapshot to be safe.
    if (status === 'connected') api.getState().then(render).catch(() => {});
  },
});
