/* Operator controls for the shared Beacon dashboard. Camera, audio rendering,
   telemetry, radar and console helpers live in the dashboard page. */
let dashboardConfig = {};
let operatorGeneration = 0, requestEpoch = 0, operatorBusy = false;
let guardianState = 'closed', operatorPolling = false, noticeId = 0;
let selectionId = null, selectedLabel = '', selectionDeadline = 0;
let pendingRequest = null, recordCap = null;
let guardianPress = Promise.resolve();
let warningSpeaking = false, warningSpeechEpoch = 0;

function warningAudioStatus() {
  $('warning-audio').textContent = obstacleWarnings.enabled ? 'Mute obstacle warnings' : 'Enable obstacle warnings';
  $('warning-audio').setAttribute('aria-pressed', String(obstacleWarnings.enabled));
  if (!obstacleWarnings.enabled) $('warning-audio-state').textContent = 'Warning audio off';
}

const obstacleWarnings = new ObstacleWarningMonitor(async (warning, current) => {
  const occupied = operatorBusy || pressed || guardianState !== 'closed' || warningSpeaking ||
    activeWebAudioSource || !audioPlayer.paused || window.speechSynthesis.speaking;
  if ((warning.simulated || warning.priority === 2) && occupied) return false;
  if (!warning.simulated && warning.priority <= 1) {
    // Stop invalidates pending replies and releases Guardian's native audio.
    const stopping = stopOperator({silent: true, keepWarnings: true});
    if (warning.priority === 0) playEarcon('error');
    await stopping;
  }
  if (!current() || guardianState !== 'closed') return false;
  silenceOperator();
  warningSpeaking = true;
  const token = ++warningSpeechEpoch;
  const utterance = new SpeechSynthesisUtterance(warning.text);
  utterance.lang = ({en:'en-US',ko:'ko-KR',zh:'zh-CN',ja:'ja-JP',es:'es-ES'})[(lastTelemetry || {}).device_lang || 'en'];
  utterance.rate = 1;
  const finish = () => {
    if (token !== warningSpeechEpoch) return;
    warningSpeaking = false;
    updateOperatorControls();
  };
  utterance.onend = finish;
  utterance.onerror = event => {
    if (token !== warningSpeechEpoch) return;
    finish();
    $('warning-audio-state').textContent = 'Warning speech unavailable: ' + event.error;
  };
  $('warning-audio-state').textContent = warning.text;
  window.speechSynthesis.speak(utterance);
  log(warning.text, warning.priority <= 1 ? 'warn' : 'info');
  updateOperatorControls();
  return true;
});

function silenceOperator() {
  ++warningSpeechEpoch;
  warningSpeaking = false;
  if (activeWebAudioSource) {
    try { activeWebAudioSource.stop(); } catch (_) {}
    activeWebAudioSource = null;
  }
  audioPlayer.pause();
  if ('speechSynthesis' in window) speechSynthesis.cancel();
}

function operatorMessage(text, speak = false) {
  statusEl.textContent = text;
  if (speak && text && !warningSpeaking) { silenceOperator(); speakFallback(text); }
}

function workflow(phase, text) {
  $('object-phase').textContent = phase.toUpperCase().replace('_', ' ');
  $('object-phase').className = 'hud-pill ' + (phase === 'found' ? 'ok' : phase === 'error' ? 'alert' : 'idle');
  $('object-status').textContent = text;
  const step = phase === 'found' ? 'confirm' : ['guiding', 'result'].includes(phase) ? 'guide' : 'find';
  for (const name of ['find', 'confirm', 'guide']) $('step-' + name).classList.toggle('current', name === step);
}

function clearSelection() {
  selectionId = null;
  selectedLabel = '';
  selectionDeadline = 0;
  $('object-preview').style.display = 'none';
  $('guide-object').textContent = 'Guide to this object';
}

function updateOperatorControls() {
  const guardian = guardianState !== 'closed';
  const locked = operatorBusy || guardian || pressed || warningSpeaking;
  for (const id of ['find-object', 'object-query', 'ask-typed', 'typed-question']) $(id).disabled = locked;
  $('guide-object').disabled = locked || !selectionId || !dashboardConfig.guidance;
  $('guide-object').title = dashboardConfig.guidance ? 'Capture a fresh view and request a route' : 'Connect the Pi for depth routing';
  $('guardian-open').disabled = operatorBusy || guardian || pressed || warningSpeaking;
  $('guardian-end').disabled = !guardian;
  for (const id of ['repeat-answer', 'local-status']) $(id).disabled = locked;
  replayBtn.disabled = locked;
  btnSoundTest.disabled = guardian || warningSpeaking;
  audioPlayer.controls = !guardian && !warningSpeaking;
  $('enable-mic').disabled = guardian || operatorBusy || warningSpeaking;
  btn.disabled = operatorBusy || warningSpeaking || (guardian ? guardianState !== 'active' : !mediaStream);
  btn.title = guardian ? 'Hold to talk to Guardian; release to send' : 'Hold to ask about the scene; release to send';
  $('operator-hint').textContent = guardian ? 'Guardian: hold the microphone button or Space for at least 0.6 seconds, then speak.' : 'Hold the microphone button or Space to talk. Release to send.';
  $('guardian-help').textContent = guardian ? `Guardian ${guardianState}. Use the talk button on the left. Texting is simulated.` : 'Uses the laptop microphone and speakers. Text messages are simulated.';
}

async function operatorAPI(path, body) {
  const response = await fetch(path, body === undefined ? {cache: 'no-store', signal: AbortSignal.timeout(8000)} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: AbortSignal.timeout(8000)
  });
  if (!response.ok) throw Error('Beacon server unavailable.');
  const data = await response.json();
  if (data.error) throw Error(data.error);
  return data;
}

async function operatorControl(action, speak = true) {
  const data = await operatorAPI('/demo/control', {action});
  operatorGeneration = data.generation;
  guardianState = data.guardian.state;
  updateOperatorControls();
  if (speak && data.answer) operatorMessage(data.answer, true);
  return data;
}

async function pollOperator() {
  if (operatorPolling) return;
  operatorPolling = true;
  try {
    const data = await operatorAPI('/demo/state');
    operatorGeneration = data.generation;
    guardianState = data.guardian.state;
    if (data.notice_id > noticeId) {
      noticeId = data.notice_id;
      operatorMessage(data.notice, true);
    }
    if (selectionId && (!data.selection || data.selection.id !== selectionId || Date.now() >= selectionDeadline)) {
      clearSelection();
      if (!operatorBusy) workflow('ready', 'Selection expired or cleared. Find the object again.');
    }
    $('observation-trail').textContent = data.trail.map(item =>
      'Camera saw ' + item.label + ' at ' + new Date(item.at * 1000).toLocaleTimeString()).join(' · ');
    $('guard-state').textContent = guardianState.toUpperCase();
    $('guard-obs').textContent = data.trail.length + ' SAVED';
    updateOperatorControls();
  } catch (error) {
    if (!warningSpeaking) silenceOperator();
    operatorMessage(error.message);
  } finally { operatorPolling = false; }
}

async function enableMic() {
  try {
    getAudioCtx();
    if (!mediaStream) mediaStream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true}});
    dot('mic', true);
    operatorMessage('Microphone ready. Hold to talk, or type a question.');
  } catch (_) {
    dot('mic', false);
    operatorMessage('Microphone unavailable. You can still type a question.', true);
  }
  updateOperatorControls();
}

function renderAnswer(data, token) {
  if (token !== requestEpoch) return;
  if (data.log) addServerLogs(data.log);
  $('r-heard').textContent = data.transcript || '';
  $('r-answer').textContent = data.answer;
  $('r-lm-wrap').style.display = data.landmark ? '' : 'none';
  $('r-lm').textContent = data.landmark || '';
  $('r-target-wrap').style.display = data.box_2d && data.target_image ? '' : 'none';
  if (data.box_2d && data.target_image) drawTarget(data.target_image, data.box_2d, data.target, 'r-target', token);
  const timings = $('r-timings');
  timings.replaceChildren();
  for (const [label, value] of [['Gemini', data.gemini_time], ['11Labs', data.el_total], ['Total', data.total_time]]) {
    if (value == null) continue;
    const span = document.createElement('span');
    span.textContent = `${label} ${value}s`;
    timings.appendChild(span);
  }
  respCard.classList.add('vis');
}

async function submitOperator(path, input) {
  if (operatorBusy || guardianState !== 'closed') return;
  const token = ++requestEpoch;
  operatorBusy = true;
  silenceOperator();
  if (path === '/find') { clearSelection(); workflow('finding', 'Looking for the object in a fresh camera view…'); }
  if (path === '/guide') { clearSelection(); workflow('guiding', 'Rechecking the object in a fresh image and requesting a route…'); }
  btn.classList.add('think');
  updateOperatorControls();
  operatorMessage(path === '/find' ? 'Finding object…' : path === '/guide' ? 'Rechecking target and planning…' : 'Thinking…');
  pendingRequest = new AbortController();
  try {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
      signal: pendingRequest.signal,
      body: JSON.stringify({...input, generation: operatorGeneration, image: captureFrame(), play_host: false})});
    if (!response.ok) throw Error('Beacon server unavailable.');
    const data = await response.json();
    if (token !== requestEpoch || data.cancelled) return;
    if (data.error) throw Error(data.error);
    dot('gemini', true);
    renderAnswer(data, token);
    if (path === '/find') {
      if (data.selection_id) {
        selectionId = data.selection_id;
        selectedLabel = data.target;
        selectionDeadline = Date.now() + data.selection_ttl_s * 1000;
        $('guide-object').textContent = 'Guide to ' + selectedLabel;
        drawTarget(data.target_image, data.box_2d, selectedLabel, 'object-preview', token);
        $('object-preview').style.display = 'block';
        workflow('found', 'I think I see the ' + selectedLabel + '. Is the highlighted object the one you want?');
        $('object-note').textContent = dashboardConfig.guidance ? 'Guide captures a fresh view before requesting a route. Selection expires after one minute.' : 'Object found. Connect the Pi to enable depth routing; this camera has no depth route.';
      } else workflow('not_found', data.answer);
    } else if (path === '/guide') {
      workflow('result', data.answer);
      if (data.box_2d && data.target_image) {
        drawTarget(data.target_image, data.box_2d, data.target, 'object-preview', token);
        $('object-preview').style.display = 'block';
      }
    }
    operatorMessage('Ready.');
    await playVoiceResponse(data.audio, data.answer, token);
  } catch (error) {
    if (token !== requestEpoch || error.name === 'AbortError') return;
    if (path !== '/ask') workflow('error', error.message);
    operatorMessage(error.message, true);
    log(error.message, 'fail');
  } finally {
    if (token === requestEpoch) { operatorBusy = false; btn.classList.remove('think'); updateOperatorControls(); }
  }
}

function cancelRecording() {
  clearTimeout(recordCap);
  if (recording) stopRec();
  pressed = false;
  btn.classList.remove('rec');
  timerEl.textContent = '';
}

async function stopOperator({silent = false, keepWarnings = false} = {}) {
  if (!keepWarnings) { obstacleWarnings.disable(); warningAudioStatus(); }
  ++requestEpoch;
  if (pendingRequest) pendingRequest.abort();
  silenceOperator();
  cancelRecording();
  clearSelection();
  workflow('ready', 'Stopped. Find an object to begin again.');
  operatorBusy = true;
  btn.classList.remove('think');
  updateOperatorControls();
  try { await operatorControl('stop', !silent); }
  catch (error) { operatorMessage(error.message, !silent); }
  finally { operatorBusy = false; updateOperatorControls(); }
}

async function onDown(event) {
  event.preventDefault();
  if (pressed || btn.disabled) return;
  pressed = true;
  silenceOperator();
  getAudioCtx();
  btn.classList.add('rec');
  if (guardianState === 'active') {
    guardianPress = operatorControl('guardian_press', false).catch(error => operatorMessage(error.message, true));
  } else {
    playEarcon('listening');
    startRec();
    operatorMessage('Listening…');
    recordCap = setTimeout(() => onUp({preventDefault() {}}), 30000);
  }
  updateOperatorControls();
}

async function onUp(event) {
  event.preventDefault();
  if (!pressed) return;
  pressed = false;
  clearTimeout(recordCap);
  btn.classList.remove('rec');
  if (guardianState === 'active') {
    await guardianPress;
    try { await operatorControl('guardian_release', false); } catch (error) { operatorMessage(error.message, true); }
  } else if (recording) {
    const wav = stopRec();
    await submitOperator('/ask', {audio: toB64(wav)});
  }
  updateOperatorControls();
}

$('object-form').onsubmit = event => { event.preventDefault(); submitOperator('/find', {text: $('object-query').value}); };
$('guide-object').onclick = () => { if (selectionId) submitOperator('/guide', {selection_id: selectionId}); };
$('question-form').onsubmit = event => { event.preventDefault(); submitOperator('/ask', {text: $('typed-question').value}); };
$('stop-all').onclick = $('stop-guidance').onclick = stopOperator;
$('enable-mic').onclick = enableMic;
$('warning-audio').onclick = () => {
  if (obstacleWarnings.enabled) {
    obstacleWarnings.disable(); silenceOperator(); updateOperatorControls();
  } else if ('speechSynthesis' in window) {
    getAudioCtx(); obstacleWarnings.enable();
    $('warning-audio-state').textContent = 'Waiting for fresh Pi warning telemetry…';
    pollDebugState();
  } else {
    $('warning-audio-state').textContent = 'Browser speech is unavailable; visual warnings remain on.';
    return;
  }
  warningAudioStatus();
};
for (const [id, action] of [['repeat-answer', 'repeat'], ['local-status', 'status']]) {
  $(id).onclick = () => operatorControl(action).catch(error => operatorMessage(error.message, true));
}
$('guardian-open').onclick = async () => {
  ++requestEpoch;
  silenceOperator();
  clearSelection();
  operatorBusy = true;
  updateOperatorControls();
  if (mediaStream) { mediaStream.getTracks().forEach(track => track.stop()); mediaStream = null; dot('mic', false); }
  try { await operatorControl('guardian_start', false); operatorMessage('Opening Guardian on the laptop…'); }
  catch (error) { operatorMessage(error.message, true); }
  finally { operatorBusy = false; updateOperatorControls(); }
};
$('guardian-end').onclick = async () => { await stopOperator(); await enableMic(); };
btn.onpointerdown = event => { btn.setPointerCapture(event.pointerId); onDown(event); };
btn.onpointerup = onUp;
btn.onpointercancel = stopOperator;
btn.oncontextmenu = event => event.preventDefault();
document.addEventListener('keydown', event => {
  if (event.code === 'Escape') { event.preventDefault(); stopOperator(); }
  if (event.code === 'Space' && !event.repeat && (!['INPUT', 'TEXTAREA', 'BUTTON'].includes(event.target.tagName) || event.target === btn)) onDown(event);
});
document.addEventListener('keyup', event => { if (event.code === 'Space' && pressed) onUp(event); });
window.addEventListener('blur', () => { if (pressed) stopOperator(); });
window.addEventListener('pagehide', () => {
  silenceOperator();
  navigator.sendBeacon('/demo/control', new Blob([JSON.stringify({action: 'stop'})], {type: 'application/json'}));
});
audioPlayer.addEventListener('play', () => { if (guardianState !== 'closed' || warningSpeaking) audioPlayer.pause(); });
setInterval(pollOperator, 750);
init();
