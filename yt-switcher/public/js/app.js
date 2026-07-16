/** UI orchestrator: renders server state pushed over the WebSocket and maps
 *  user actions to REST calls. DOM nodes for cards are reused across renders
 *  (keyed by source id) so thumbnails never flicker or re-download.
 *
 *  Hotkeys: Numpad 1-9 = Sources 1-9, Numpad 0 = Source 10, A-J = Sources 11-20.
 *  DVR: Left/Right arrow = seek ±10s, Shift+Arrow = ±30s, Space = play/pause.
 */

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
  programStatus: $('programStatus'),
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
  vcamDeviceSelect: $('vcamDeviceSelect'),
  monitorStatus: $('monitorStatus'),
  volume: $('volume'),
  volumeLabel: $('volumeLabel'),
  btnMute: $('btnMute'),
  speedSelect: $('speedSelect'),
  selectedInfo: $('selectedInfo'),
  connState: $('connState'),
  procTable: $('procTable'),
  // DVR
  dvrControls: $('dvrControls'),
  dvrTimeline: $('dvrTimeline'),
  timelineTooltip: $('timelineTooltip'),
  dvrTime: $('dvrTime'),
  dvrRemaining: $('dvrRemaining'),
  // Performance
  perfEncoder: $('perfEncoder'),
  perfOutputFps: $('perfOutputFps'),
  perfRenderFps: $('perfRenderFps'),
  perfFrameTime: $('perfFrameTime'),
  perfDropped: $('perfDropped'),
  perfSkipped: $('perfSkipped'),
  perfBitrate: $('perfBitrate'),
  perfBuffer: $('perfBuffer'),
  perfFfmpegFps: $('perfFfmpegFps'),
  perfSpeed: $('perfSpeed'),
  // Hotkeys
  hotkeyOverlay: $('hotkeyOverlay'),
};

let state = null;
const cardNodes = new Map(); // source id -> element
let previewAttached = false;
let dvrScrubbing = false; // true when user is dragging the timeline slider
let dvrSeeking = false;   // true after scrub release until mpv confirms position
let lastKnownDuration = 0; // cached duration for scrub-time calculations

/* ============================== HOTKEY MAP ============================== */

const HOTKEY_MAP = {
  // Numpad 1-9 → sources[0]-sources[8]
  'Numpad1': 0, 'Numpad2': 1, 'Numpad3': 2, 'Numpad4': 3, 'Numpad5': 4,
  'Numpad6': 5, 'Numpad7': 6, 'Numpad8': 7, 'Numpad9': 8,
  // Numpad 0 → source 10 (sources[9])
  'Numpad0': 9,
  // A-J → sources[10]-sources[19]
  'KeyA': 10, 'KeyB': 11, 'KeyC': 12, 'KeyD': 13, 'KeyE': 14,
  'KeyF': 15, 'KeyG': 16, 'KeyH': 17, 'KeyI': 18, 'KeyJ': 19,
};

// Source index label shown on cards
function sourceHotkeyLabel(index) {
  if (index < 9) return `${index + 1}`;  // 1-9
  if (index === 9) return '0';            // Numpad 0
  if (index < 20) return String.fromCharCode(65 + index - 10); // A-J
  return '';
}

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
    updateCard(card, source, index);
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

function updateCard(card, source, index) {
  const img = card.querySelector('.thumb img');
  const thumbUrl = source.thumbnail || '';
  if (img.getAttribute('src') !== thumbUrl) img.setAttribute('src', thumbUrl);

  setText(card.querySelector('.card-title'), source.title);
  setText(card.querySelector('.card-sub'), source.channel || hostOf(source.url));

  // Hotkey index badge
  const indexEl = card.querySelector('.card-index');
  const label = sourceHotkeyLabel(index);
  setText(indexEl, label);

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
  const { programId, status, error, isPaused, isLive } = state.program;
  const source = state.sources.find((s) => s.id === programId);
  const active = status === 'playing' || status === 'starting' || status === 'paused';

  els.programTally.classList.toggle('on', status === 'playing');
  els.programTally.classList.toggle('paused', status === 'paused');
  setText(els.programSourceTitle, source ? `— ${source.title}` : '');
  setText(els.programStatus, status === 'paused' ? '⏸ PAUSED' : '');

  // Only hold the MJPEG connection open while a program is actually running.
  if ((status === 'playing' || status === 'starting') && !previewAttached) {
    els.programPreview.src = '/api/preview.mjpeg?t=' + Date.now();
    els.programPreview.classList.add('visible');
    previewAttached = true;
  } else if (status !== 'playing' && status !== 'starting' && status !== 'paused' && previewAttached) {
    els.programPreview.src = '';
    els.programPreview.classList.remove('visible');
    previewAttached = false;
  }

  const overlayMsgs = {
    idle: 'NO PROGRAM — select a source below',
    resolving: 'RESOLVING STREAM…',
    starting: 'CONNECTING…',
    paused: '⏸ PAUSED',
    error: error ? `ERROR — ${error.message}` : 'ERROR',
  };
  if (status === 'playing') {
    els.programOverlay.classList.add('hidden');
  } else if (status === 'paused') {
    els.programOverlay.classList.remove('hidden');
    setText(els.overlayText, overlayMsgs.paused);
    els.overlayText.classList.remove('error');
  } else {
    els.programOverlay.classList.remove('hidden');
    setText(els.overlayText, overlayMsgs[status] || status);
    els.overlayText.classList.toggle('error', status === 'error');
  }

  setText(els.infoLive, source ? (source.isLive ? 'LIVE' : 'VOD') : '—');
  els.infoLive.classList.toggle('live', Boolean(source && source.isLive));

  // Show/hide DVR controls based on source type
  const showDvr = active && source && !source.isLive;
  els.dvrControls.classList.toggle('hidden', !showDvr);

  // Update play/pause button states
  $('btnPlay').classList.toggle('active', status === 'playing');
  $('btnPause').classList.toggle('active', status === 'paused');
}

function renderSidePanel() {
  const v = state.vcam;
  setText(els.vcamFormat, `${v.width}×${v.height} @ ${v.fps}fps`);
  setPill(els.vcamStatus, v.status);
  setPill(els.monitorStatus, state.monitor.status);

  if (els.vcamDeviceSelect && v.availableDevices) {
    const activeDev = v.device || 'OBS-Camera';
    const currentOpts = Array.from(els.vcamDeviceSelect.options).map((o) => o.value);
    const optionsChanged =
      currentOpts.length !== v.availableDevices.length ||
      !v.availableDevices.every((d, i) => d === currentOpts[i]);

    if (optionsChanged) {
      els.vcamDeviceSelect.innerHTML = '';
      for (const devName of v.availableDevices) {
        const opt = document.createElement('option');
        opt.value = devName;
        opt.textContent = devName;
        els.vcamDeviceSelect.appendChild(opt);
      }
    }
    if (document.activeElement !== els.vcamDeviceSelect) {
      els.vcamDeviceSelect.value = activeDev;
    }
  }

  if (v.notification) {
    showError(new Error(v.notification));
  }

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

  // Update performance panel from stats
  if (stats.performance) {
    renderPerformance(stats.performance);
  }
}

function renderPerformance(perf) {
  setPill(els.perfEncoder, perf.encoderStatus);
  setText(els.perfOutputFps, perf.outputFps ? `${perf.outputFps.toFixed(1)}` : '—');
  setText(els.perfRenderFps, perf.renderFps ? `${perf.renderFps.toFixed(1)}` : '—');
  setText(els.perfFrameTime, perf.frameTimeMs ? `${perf.frameTimeMs.toFixed(1)} ms` : '—');
  setText(els.perfDropped, String(perf.droppedFrames || 0));
  setText(els.perfSkipped, String(perf.skippedFrames || 0));
  setText(els.perfBitrate, perf.videoBitrate || '—');
  setText(els.perfBuffer, perf.bufferSize ? `${(perf.bufferSize / 1024).toFixed(0)} KB` : '0 KB');
  setText(els.perfFfmpegFps, perf.ffmpegFps ? `${perf.ffmpegFps.toFixed(1)}` : '—');
  setText(els.perfSpeed, perf.ffmpegSpeed || '—');

  // Color-code FPS values
  const fps = perf.renderFps || 0;
  els.perfRenderFps.className = 'mono-val ' + (fps >= 28 ? 'val-ok' : fps >= 20 ? 'val-warn' : fps > 0 ? 'val-err' : '');
  els.perfDropped.className = 'mono-val ' + (perf.droppedFrames > 0 ? 'val-warn' : '');
  els.perfSkipped.className = 'mono-val ' + (perf.skippedFrames > 0 ? 'val-warn' : '');
}

function renderMpvProps(props) {
  const vp = props['video-params'];
  setText(els.infoRes, vp && vp.w ? `${vp.w}×${vp.h}` : '—');
  const fps = props['estimated-vf-fps'];
  setText(els.infoFps, fps ? `${fps.toFixed(1)} fps` : '— fps');
  const cache = props['demuxer-cache-duration'];
  setText(els.infoBuffer, cache != null ? `buf ${cache.toFixed(1)}s` : 'buf —');
  setText(els.infoTime, fmtTime(props['time-pos']));

  if (props.speed != null && document.activeElement !== els.speedSelect) {
    els.speedSelect.value = String(props.speed);
  }

  // Track duration for scrub calculations
  if (props['duration'] != null && props['duration'] > 0) {
    lastKnownDuration = props['duration'];
  }

  // Clear seeking indicator once mpv reports the updated position
  if (dvrSeeking && props['time-pos'] != null) {
    dvrSeeking = false;
  }

  // Update DVR timeline if not actively scrubbing or waiting for seek
  if (!dvrScrubbing && !dvrSeeking) {
    const pos = props['time-pos'];
    const dur = props['duration'];
    if (pos != null && dur != null && dur > 0) {
      const pct = (pos / dur) * 100;
      els.dvrTimeline.value = pct;
      setText(els.dvrTime, `${fmtTime(pos)} / ${fmtTime(dur)}`);
    }
    const remaining = props['playtime-remaining'];
    setText(els.dvrRemaining, remaining != null ? `-${fmtTime(remaining)}` : '--:--');
  }
}

/* ------------------------------- helpers ------------------------------- */

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text ?? '';
}

function setPill(el, status) {
  if (!el) return;
  setText(el, status);
  el.className = 'status-pill ' + (
    status === 'running' || status === 'playing' ? 'ok'
    : status === 'failed' || status === 'error' ? 'err'
    : status === 'disabled' || status === 'stopped' || status === 'idle' ? ''
    : 'warn'
  );
}

function fmtTime(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--';
  const s = Math.floor(Math.abs(seconds));
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

/** Flash a card briefly for hotkey visual feedback. */
function flashCard(card) {
  card.classList.add('hotkey-flash');
  setTimeout(() => card.classList.remove('hotkey-flash'), 300);
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
els.speedSelect.addEventListener('change', () => {
  api.setSpeed(Number(els.speedSelect.value)).catch(showError);
});
$('btnFullscreen').addEventListener('click', () => {
  const screen = $('programScreen');
  if (document.fullscreenElement) document.exitFullscreen();
  else screen.requestFullscreen().catch(() => {});
});
$('btnMonitorToggle').addEventListener('click', () => api.toggleMonitor().catch(showError));
$('btnVcamReset').addEventListener('click', () => api.resetVcam().catch(showError));
if (els.vcamDeviceSelect) {
  els.vcamDeviceSelect.addEventListener('change', () => {
    api.setVcamDevice(els.vcamDeviceSelect.value).catch(showError);
  });
}

// --- DVR Controls ---
$('btnSeekBack').addEventListener('click', () => api.seekBackward(10).catch(showError));
$('btnSeekFwd').addEventListener('click', () => api.seekForward(10).catch(showError));

els.dvrTimeline.addEventListener('input', () => {
  dvrScrubbing = true;
  // Show the computed target time immediately for responsive scrubbing feedback
  if (lastKnownDuration > 0) {
    const pct = Number(els.dvrTimeline.value);
    const targetTime = (pct / 100) * lastKnownDuration;
    setText(els.dvrTime, `${fmtTime(targetTime)} / ${fmtTime(lastKnownDuration)}`);
    const remaining = lastKnownDuration - targetTime;
    setText(els.dvrRemaining, `-${fmtTime(remaining)}`);
  }
});
els.dvrTimeline.addEventListener('change', () => {
  dvrScrubbing = false;
  dvrSeeking = true; // Hold position display until mpv confirms
  api.seekPercent(Number(els.dvrTimeline.value)).catch(showError);
});

els.dvrTimeline.addEventListener('mousemove', (ev) => {
  if (lastKnownDuration <= 0) return;
  const rect = els.dvrTimeline.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
  const hoverTime = pct * lastKnownDuration;
  const formatted = fmtTime(hoverTime);
  els.dvrTimeline.title = `${formatted} / ${fmtTime(lastKnownDuration)}`;
  if (els.timelineTooltip) {
    els.timelineTooltip.textContent = formatted;
    els.timelineTooltip.style.left = `${pct * 100}%`;
    els.timelineTooltip.classList.remove('hidden');
  }
});
els.dvrTimeline.addEventListener('mouseleave', () => {
  if (els.timelineTooltip) {
    els.timelineTooltip.classList.add('hidden');
  }
});

// --- Hotkey Legend ---
$('closeHotkeys').addEventListener('click', () => {
  els.hotkeyOverlay.classList.add('hidden');
});
els.hotkeyOverlay.addEventListener('click', (ev) => {
  if (ev.target === els.hotkeyOverlay) {
    els.hotkeyOverlay.classList.add('hidden');
  }
});

/* ========================== KEYBOARD SHORTCUTS ========================== */

document.addEventListener('keydown', (ev) => {
  // Skip hotkeys when typing in an input/textarea
  const tag = ev.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (ev.ctrlKey || ev.altKey || ev.metaKey) return;

  // ? = Toggle hotkey legend
  if (ev.key === '?' || (ev.code === 'Slash' && ev.shiftKey)) {
    ev.preventDefault();
    els.hotkeyOverlay.classList.toggle('hidden');
    return;
  }

  // Escape = close hotkey overlay or stop program
  if (ev.key === 'Escape') {
    if (!els.hotkeyOverlay.classList.contains('hidden')) {
      els.hotkeyOverlay.classList.add('hidden');
    } else {
      api.stopProgram().catch(showError);
    }
    return;
  }

  // Space = Play/Pause toggle
  if (ev.code === 'Space') {
    ev.preventDefault();
    if (state && (state.program.status === 'playing')) {
      api.playback('pause').catch(showError);
    } else {
      api.playback('play').catch(showError);
    }
    return;
  }

  // M = Mute toggle
  if (ev.code === 'KeyM' && !ev.shiftKey) {
    ev.preventDefault();
    api.playback(state && state.settings.muted ? 'unmute' : 'mute').catch(showError);
    return;
  }

  // F = Fullscreen (only when not mapped to source switching)
  // F is KeyF which is mapped to source 16, so we use it differently:
  // Only process F as fullscreen when program is playing and there are < 16 sources
  // Actually, since F is mapped to source 16, we'll skip F for fullscreen
  // Users can still click the fullscreen button or use the existing KeyF for source 16

  // Arrow keys = DVR seek (VOD only)
  if (ev.key === 'ArrowLeft') {
    ev.preventDefault();
    const secs = ev.shiftKey ? 30 : 10;
    api.seekBackward(secs).catch(showError);
    return;
  }
  if (ev.key === 'ArrowRight') {
    ev.preventDefault();
    const secs = ev.shiftKey ? 30 : 10;
    api.seekForward(secs).catch(showError);
    return;
  }

  // Source switching hotkeys
  const sourceIndex = HOTKEY_MAP[ev.code];
  if (sourceIndex !== undefined && state && state.sources[sourceIndex]) {
    ev.preventDefault();
    const source = state.sources[sourceIndex];
    api.setProgram(source.id).catch(showError);
    // Visual feedback — flash the card
    const card = cardNodes.get(source.id);
    if (card) flashCard(card);
    return;
  }
});

/* ------------------------------- startup ------------------------------- */

connectWs({
  onMessage(type, payload) {
    if (type === 'state') render(payload);
    else if (type === 'stats') renderStats(payload);
    else if (type === 'mpvProps') renderMpvProps(payload);
    else if (type === 'perfUpdate') renderPerformance(payload);
  },
  onStatus(status) {
    setText(els.connState, status === 'connected' ? '● connected' : '○ reconnecting…');
    els.connState.className = 'conn ' + (status === 'connected' ? 'ok' : 'err');
    // After a reconnect the push stream resumes; fetch a snapshot to be safe.
    if (status === 'connected') api.getState().then(render).catch(() => {});
  },
});
