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
