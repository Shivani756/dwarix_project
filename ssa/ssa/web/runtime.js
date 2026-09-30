"use strict";

(function installVoiceRuntime(root) {
  const EMAIL_RE = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
  const PHONE_RE = /(?<!\d)\+?\d(?:[\s().-]*\d){9,14}(?!\d)/g;

  function redactPii(value) {
    return String(value ?? "")
      .replace(EMAIL_RE, "[REDACTED_EMAIL]")
      .replace(PHONE_RE, "[REDACTED_PHONE]");
  }

  class SessionTurnQueue {
    constructor() {
      this.tail = Promise.resolve();
    }

    enqueue(sessionId, isCurrent, task) {
      const result = this.tail.then(() => (isCurrent(sessionId) ? task() : undefined));
      this.tail = result.catch(() => undefined);
      return result;
    }

    drain() {
      return this.tail;
    }
  }

  function activeNudges(nudges, now = Date.now()) {
    return (Array.isArray(nudges) ? nudges : []).filter((item) => {
      const expires = Date.parse(item.expires_at);
      return Number.isFinite(expires) && expires > now;
    });
  }

  function nextNudgeExpiryMs(nudges, now = Date.now()) {
    const next = activeNudges(nudges, now).reduce((earliest, item) => Math.min(earliest, Date.parse(item.expires_at)), Infinity);
    return Number.isFinite(next) ? Math.max(0, next - now) : null;
  }

  const runtime = { redactPii, SessionTurnQueue, activeNudges, nextNudgeExpiryMs };
  if (typeof module !== "undefined" && module.exports) module.exports = runtime;
  else root.VoiceRuntime = runtime;
})(typeof globalThis !== "undefined" ? globalThis : window);
