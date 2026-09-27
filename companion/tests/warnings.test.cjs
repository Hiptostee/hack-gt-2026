const test = require('node:test');
const assert = require('node:assert/strict');
const {warningFromTelemetry, ObstacleWarningMonitor} = require('../dashboard/warnings.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const live = (overrides = {}) => ({hazard: {available: true, age_s: .02, urgent: true,
  phrase: 'Stop. Obstacle ahead.', warning_ttl_ms: 200, ...overrides}});

test('network transit consumes warning lifetime; expired evidence is silent', () => {
  assert.equal(warningFromTelemetry(live(), 200), null);
  assert.equal(warningFromTelemetry(live(), 250), null);
  assert.equal(warningFromTelemetry(live(), 50).ttlMs, 150);
  assert.equal(warningFromTelemetry(live({warning_ttl_ms: undefined})), null);
});
test('missing or stale sensing announces unavailable, never clear', () => {
  assert.match(warningFromTelemetry({}).text, /unavailable/);
  assert.match(warningFromTelemetry(live({age_s: .6})).text, /unavailable/);
  assert.equal(warningFromTelemetry(live({urgent: false})), null);
});
test('simulation is explicitly identified and independent of live evidence TTL', () => {
  const data = live({warning_ttl_ms: undefined, simulated:true}); data.simulation_active = true;
  const warning = warningFromTelemetry(data);
  assert.equal(warning.simulated, true);
  assert.match(warning.text, /^Simulated warning\./);
});
test('warnings require opt-in and repeat only at the severity interval', async () => {
  let now = 0, calls = 0;
  const monitor = new ObstacleWarningMonitor(async () => {calls++; return true;}, () => now);
  await monitor.update(live()); assert.equal(calls, 0);
  monitor.enable(); await monitor.update(live()); assert.equal(calls, 1);
  now = 1999; await monitor.update(live()); assert.equal(calls, 1);
  now = 2000; await monitor.update(live()); assert.equal(calls, 2);
  monitor.disable(); now = 5000; await monitor.update(live()); assert.equal(calls, 2);
});
test('caution uses three seconds and unavailable uses fifteen seconds', async () => {
  for (const [data, interval] of [[live({urgent:false,caution:true}),3000],[{},15000]]) {
    let now = 0, calls = 0;
    const monitor = new ObstacleWarningMonitor(async () => {calls++; return true;}, () => now);
    monitor.enable(); await monitor.update(data);
    now = interval - 1; await monitor.update(data); assert.equal(calls, 1);
    now = interval; await monitor.update(data); assert.equal(calls, 2);
  }
});
test('a delayed audio handoff rechecks fresh evidence and Stop cancels it', async () => {
  let now = 0, check, release;
  const monitor = new ObstacleWarningMonitor(async (_, current) => {
    check = current; await new Promise(resolve => {release = resolve;}); return current();
  }, () => now);
  monitor.enable(); const pending = monitor.update(live());
  assert.ok(check()); now = 201; assert.equal(check(), false);
  await monitor.update(live()); assert.ok(check());
  monitor.disable(); assert.equal(check(), false);
  release(); await pending;
});
test('busy audio can skip an alert without queueing or consuming its repeat interval', async () => {
  let busy = true, calls = 0;
  const monitor = new ObstacleWarningMonitor(async () => {calls++; return !busy;}, () => 0);
  monitor.enable(); await monitor.update(live()); busy = false;
  await monitor.update(live()); assert.equal(calls, 2);
});

function operator(guardian = 'closed') {
  const source = fs.readFileSync(path.join(__dirname, '../dashboard/controls.js'), 'utf8');
  const calls = {stops: [], speech: [], utterances: []};
  const context = vm.createContext({ObstacleWarningMonitor, operatorBusy:false, pressed:false,
    guardianState:guardian, warningSpeaking:false, warningSpeechEpoch:0, activeWebAudioSource:null,
    audioPlayer:{paused:true}, lastTelemetry:{device_lang:'en'},
    window:{speechSynthesis:{speaking:false,speak: u => {calls.speech.push(u.text); calls.utterances.push(u);}}},
    SpeechSynthesisUtterance:class {constructor(text) {this.text=text;}},
    silenceOperator() {}, playEarcon() {}, updateOperatorControls() {}, log() {}, $: () => ({})});
  context.stopOperator = async options => {calls.stops.push(options); context.guardianState='closed';};
  vm.runInContext(source.slice(source.indexOf('const obstacleWarnings ='), source.indexOf('function silenceOperator()')) +
    '\nglobalThis.monitor = obstacleWarnings;', context);
  context.monitor.enable();
  return {context, calls};
}

test('live urgent warning stops the operator and releases Guardian before speaking', async () => {
  const {context, calls} = operator('active');
  await context.monitor.update(live());
  assert.equal(calls.stops.length, 1);
  assert.equal(calls.stops[0].silent, true);
  assert.equal(calls.stops[0].keepWarnings, true);
  assert.deepEqual(calls.speech, ['Stop. Obstacle ahead.']);
});
test('simulated warning never stops guidance or Guardian', async () => {
  const data = {...live({simulated:true}), simulation_active:true};
  const idle = operator(); await idle.context.monitor.update(data);
  assert.equal(idle.calls.stops.length, 0);
  assert.match(idle.calls.speech[0], /^Simulated warning/);
  const guardian = operator('active'); await guardian.context.monitor.update(data);
  assert.equal(guardian.calls.stops.length, 0);
  assert.equal(guardian.calls.speech.length, 0);
});
test('warning speech is discarded when evidence expires during native audio handoff', async () => {
  const {context, calls} = operator('active');
  let now = 0; context.monitor.now = () => now;
  context.stopOperator = async () => {now = 300; context.guardianState='closed';};
  await context.monitor.update(live());
  assert.equal(calls.speech.length, 0);
});
test('intentional Stop does not turn a cancelled warning into an audio error', async () => {
  const {context, calls} = operator();
  const status = {}; context.$ = () => status;
  await context.monitor.update({...live({simulated:true}), simulation_active:true});
  context.warningSpeechEpoch++;
  status.textContent = 'Warning audio off';
  calls.utterances[0].onerror({error:'interrupted'});
  assert.equal(status.textContent, 'Warning audio off');
});
test('a simulated direction does not relabel a real depth warning as simulated', () => {
  assert.equal(warningFromTelemetry({...live(), simulation_active:true}).simulated, false);
});
