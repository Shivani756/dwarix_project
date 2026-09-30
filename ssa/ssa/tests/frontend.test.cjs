const assert = require("node:assert/strict");
const { test } = require("node:test");
const { redactPii, SessionTurnQueue, activeNudges, nextNudgeExpiryMs } = require("../web/runtime.js");

test("transcript export redacts common email and phone patterns but keeps amounts", () => {
  const clean = redactPii("Email person@example.com or call 09171234567 about PHP 1,500.");
  assert.equal(clean, "Email [REDACTED_EMAIL] or call [REDACTED_PHONE] about PHP 1,500.");
});

test("turn queue serializes utterances and discards work from an old call", async () => {
  const queue = new SessionTurnQueue();
  const order = [];
  let currentSession = "a";
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const first = queue.enqueue("a", (id) => id === currentSession, async () => { order.push("first-start"); await gate; order.push("first-end"); });
  const second = queue.enqueue("a", (id) => id === currentSession, async () => { order.push("second"); });
  const stale = queue.enqueue("a", (id) => id === currentSession, async () => { order.push("stale"); });
  await Promise.resolve();
  currentSession = "b";
  release();
  await Promise.all([first, second, stale]);
  assert.deepEqual(order, ["first-start", "first-end"]);
});

test("visible nudges are filtered at expiry and expose the next expiry delay", () => {
  const now = Date.parse("2026-09-29T12:00:00Z");
  const nudges = [
    { id: "live", expires_at: "2026-09-29T12:00:05Z" },
    { id: "expired", expires_at: "2026-09-29T11:59:59Z" },
  ];
  assert.deepEqual(activeNudges(nudges, now).map((item) => item.id), ["live"]);
  assert.equal(nextNudgeExpiryMs(nudges, now), 5000);
});
