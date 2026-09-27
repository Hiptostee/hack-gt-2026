// Exercise the actual dashboard renderer without cloud calls or a browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../voice/web_test.py'), 'utf8');
const render = source.slice(source.indexOf('function updateHud('), source.indexOf('setInterval(pollDebugState, 333)'));

function dashboard() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
    return elements.get(id);
  };
  const context = vm.createContext({$: get, document: {querySelectorAll: () => []}});
  vm.runInContext(render, context);
  return {get, update: data => context.updateHud(data, 10)};
}

test('ROS rotation values render left and right, never arrival', () => {
  const d = dashboard();
  for (const [direction, label] of [[3, 'ROTATE LEFT'], [4, 'ROTATE RIGHT']]) {
    d.update({guidance: {active: true, path_valid: true, direction}});
    assert.equal(d.get('dir-title').textContent, label);
  }
});

test('lost telemetry clears previous direction, hands and measured values', () => {
  const d = dashboard();
  d.update({camera: {status: 'Ready'}, guidance: {active: true, path_valid: true, direction: 0, tactile_flags: 1},
    vitals: {depth_fps: 15, depth_latency_ms: 12, hazard_loop_ms: 9, planner_loop_ms: 20}});
  d.update({telemetry_unavailable: true});
  assert.equal(d.get('dir-title').textContent, 'IDLE / CANE ONLY');
  assert.equal(d.get('hud-tactile-flags').textContent, '0x00 NEUTRAL');
  assert.equal(d.get('sim-pill').textContent, 'TELEMETRY UNAVAILABLE');
  for (const id of ['vital-cam', 'vital-haz', 'vital-plan']) assert.equal(d.get(id).textContent, 'UNMEASURED');
  assert.equal(d.get('hazard-pill').textContent, 'NO SENSING');
});

test('missing camera health and latency do not become positive defaults', () => {
  const d = dashboard();
  d.update({vitals: {depth_fps: 15}, camera: {status: 'Pi bridge unavailable'}});
  assert.equal(d.get('hud-cam-status').className, 'hud-pill alert');
  assert.equal(d.get('vital-cam').textContent, '15 FPS · unmeasured');
});


test('simulated hazard stays labelled beside the warning message', () => {
  const d = dashboard();
  d.update({hazard: {available: true, urgent: true, simulated: true, phrase: 'Head obstacle.'}});
  assert.equal(d.get('hazard-msg').textContent, 'Simulated warning. Head obstacle.');
  d.update({simulation_active: true, hazard: {available: true, urgent: true, phrase: 'Live obstacle.'}});
  assert.equal(d.get('hazard-msg').textContent, 'Live obstacle.');
  d.update({telemetry_unavailable: true});
  assert.equal(d.get('hazard-card').className, 'hud-card hazard-card unavailable');
});

test('returning from radar preserves static and missing-camera labels', () => {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {style: {}, textContent: ''});
    return elements.get(id);
  };
  const context = vm.createContext({$: get, dashboardConfig: {fallback_image: true},
    hasWebcam: false, fallback: {complete: false, naturalWidth: 0}, lastTelemetry: {},
    drawPlannerRadar() {}});
  vm.runInContext(source.slice(source.indexOf('function switchLeftView('), source.indexOf('function drawPlannerRadar(')), context);
  context.switchLeftView('map');
  context.switchLeftView('cam');
  assert.equal(get('cam-tag').textContent, 'Static rehearsal image');
  assert.equal(get('fallback').style.display, 'block');
  context.dashboardConfig = {};
  context.switchLeftView('cam');
  assert.equal(get('cam-tag').textContent, 'No camera');
  assert.equal(get('fallback').style.display, 'none');
});

test('Space on simulator disclosure retains native keyboard activation', () => {
  const controls = fs.readFileSync(path.join(__dirname, '../dashboard/controls.js'), 'utf8');
  let keydown, talks = 0;
  const mic = {};
  const context = vm.createContext({document: {addEventListener(name, handler) {keydown = handler;}},
    btn: mic, onDown() {talks++;}, stopOperator() {}});
  vm.runInContext(controls.slice(controls.indexOf("document.addEventListener('keydown'"),
    controls.indexOf("document.addEventListener('keyup'")), context);
  keydown({code: 'Space', repeat: false, target: {tagName: 'SUMMARY'}});
  assert.equal(talks, 0);
  keydown({code: 'Space', repeat: false, target: mic});
  assert.equal(talks, 1);
});
