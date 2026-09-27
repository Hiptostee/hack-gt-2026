/* Fixed-phrase laptop warning demo. No cloud reasoning or movement permission. */
function warningFromTelemetry(data, elapsedMs = 0) {
  const h = data.hazard || {};
  const simulated = h.simulated === true;
  let text, priority, repeatMs, ttlMs;
  if (!h.available || h.age_s == null || h.age_s * 1000 + elapsedMs > 500) {
    text = h.unavailable_phrase || 'Hazard sensing unavailable. Use your cane.';
    priority = 1; repeatMs = 15000; ttlMs = 2000;
  } else if (h.urgent || h.caution) {
    if (!h.phrase) return null;
    ttlMs = simulated ? 2000 : h.warning_ttl_ms - elapsedMs;
    if (!Number.isFinite(ttlMs) || ttlMs <= 0) return null;
    text = h.phrase;
    priority = h.urgent ? 0 : 2;
    repeatMs = h.urgent ? 2000 : 3000;
  } else return null;
  if (simulated) text = 'Simulated warning. ' + text;
  return {text, priority, repeatMs, ttlMs, simulated, key: `${simulated}:${priority}:${text}`};
}

class ObstacleWarningMonitor {
  constructor(announce, now = () => performance.now()) {
    this.announce = announce; this.now = now;
    this.enabled = false; this.epoch = 0; this.pending = false;
    this.latest = null; this.lastKey = ''; this.lastAt = -Infinity;
  }
  enable() { this.enabled = true; this.epoch++; this.lastKey = ''; }
  disable() { this.enabled = false; this.epoch++; this.latest = null; }
  async update(data, elapsedMs = 0) {
    const warning = warningFromTelemetry(data, elapsedMs);
    this.latest = warning ? {...warning, expires: this.now() + warning.ttlMs} : null;
    if (!this.enabled || !warning || this.pending) return;
    if (this.lastKey === warning.key && this.now() - this.lastAt < warning.repeatMs) return;
    const epoch = this.epoch;
    const current = () => this.enabled && epoch === this.epoch && this.latest &&
      this.latest.key === warning.key && this.now() < this.latest.expires;
    this.pending = true;
    try {
      if (await this.announce(warning, current)) {
        this.lastKey = warning.key; this.lastAt = this.now();
      }
    } finally { this.pending = false; }
  }
}
if (typeof module !== 'undefined') module.exports = {warningFromTelemetry, ObstacleWarningMonitor};
