// Behavior tests for external-action boundaries; no browser or paid APIs required.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const staticDir = path.join(__dirname, '../static');
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');

function app() {
  const elements = new Map();
  for (const match of html.matchAll(/id="([^"]+)"/g)) {
    elements.set(match[1], { value: '', textContent: '', hidden: false, listeners: {},
      addEventListener(event, action) { this.listeners[event] = action; },
      focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
      removeAttribute(name) { delete this[name]; } });
  }
  const timers = new Map(); let nextTimer = 0;
  const storage = new Map(), shared = [];
  const context = vm.createContext({
    document: { getElementById: id => elements.get(id), querySelectorAll: () => [] },
    window: { location: { href: '' }, addEventListener() {} },
    navigator: { language: 'en-US', userAgent: 'test-phone', canShare: () => true,
      share: async data => { shared.push(data); } },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key) },
    setTimeout: (action, delay) => { const id = ++nextTimer; timers.set(id, { action, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async () => { throw new Error('Network unavailable'); },
    AbortSignal, File, Uint8Array, atob, isSecureContext: true, console
  });
  vm.runInContext(script, context);
  return { context, shared, timers, get: id => elements.get(id),
    run: code => vm.runInContext(code, context) };
}

test('call never opens before confirmation, and cancel has no side effect', async () => {
  const a = app(); a.get('contact').value = '+1 404 555 0100';
  await a.get('call').onclick();
  assert.equal(a.context.window.location.href, '');
  assert.equal(a.get('confirm-dialog').open, true);
  a.get('cancel-action').onclick();
  assert.equal(a.context.window.location.href, '');
  await a.get('call').onclick(); a.get('confirm-action').onclick();
  assert.equal(a.context.window.location.href, 'tel:+14045550100');
});

test('help tap requires confirmation; interrupted hold never opens controls', () => {
  const a = app(); a.get('help-controls').hidden = true;
  a.get('help-open').onpointerdown({ button: 0 });
  assert.equal(a.timers.size, 1);
  a.get('help-open').listeners.pointercancel();
  assert.equal(a.timers.size, 0);
  assert.equal(a.get('help-controls').hidden, true);
  a.get('help-open').onclick();
  assert.equal(a.get('help-controls').hidden, true);
  a.get('confirm-action').onclick();
  assert.equal(a.get('help-controls').hidden, false);
});

test('full hold opens controls but never calls anyone', () => {
  const a = app(); a.get('help-controls').hidden = true;
  a.get('help-open').onpointerdown({ button: 0 });
  const timer = [...a.timers.values()][0];
  assert.equal(timer.delay, 2000); timer.action();
  assert.equal(a.get('help-controls').hidden, false);
  assert.equal(a.context.window.location.href, '');
});

test('stale phone location cannot open a message', async () => {
  const a = app(); a.get('contact').value = '+14045550100';
  a.run('locationFix = {lat: 33, lon: -84, accuracy: 20, timestamp: Date.now() - 121000}');
  await a.get('send-location').onclick();
  assert.match(a.get('notice').textContent, /fresh phone location/);
  assert.equal(a.context.window.location.href, '');
});

test('location expiring during confirmation cannot be sent', async () => {
  const a = app(); a.get('contact').value = '+14045550100';
  a.run('locationFix = {lat: 33, lon: -84, accuracy: 20, timestamp: Date.now()}');
  await a.get('send-location').onclick();
  assert.equal(a.context.window.location.href, '');
  a.run('locationFix.timestamp = Date.now() - 121000');
  a.get('confirm-action').onclick();
  assert.equal(a.context.window.location.href, '');
  assert.match(a.get('notice').textContent, /fresh phone location/);
});

test('photo share requires confirmation and attaches exactly one image, no location', async () => {
  const a = app();
  a.run('scene = {id: "one", source: "upload", data_url: "data:image/jpeg;base64,/9j/dGVzdA==", expires_in: 600, selectedAt: Date.now()}');
  await a.get('share-scene').onclick();
  assert.equal(a.shared.length, 0);
  a.get('cancel-action').onclick();
  assert.equal(a.shared.length, 0);
  await a.get('share-scene').onclick(); a.get('confirm-action').onclick();
  assert.equal(a.shared.length, 1);
  assert.equal(a.shared[0].files.length, 1);
  assert.match(a.shared[0].text, /capture time is unknown/);
  assert.doesNotMatch(a.shared[0].text, /maps.google|latitude|longitude/);
});

test('cloud/network error does not disable help controls', async () => {
  const a = app();
  a.run('scene = {id: "one", expires_in: 600, selectedAt: Date.now()}');
  await a.get('describe').onclick();
  assert.match(a.get('notice').textContent, /Network unavailable/);
  a.get('help-open').onclick(); a.get('confirm-action').onclick();
  assert.equal(a.get('help-controls').hidden, false);
});
