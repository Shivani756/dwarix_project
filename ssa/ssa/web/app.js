"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  sessionId: null,
  locale: "en-PH",
  recognition: null,
  mediaStream: null,
  mediaRecorder: null,
  audioChunks: [],
  transcript: [],
  lastSpeechStart: 0,
  callActive: false,
  speechRecognized: false,
  acceptingFinalSpeech: false,
  lastResult: null,
  citations: new Map(),
  scenarioBusy: false,
  closingRequested: false,
  turnQueue: new VoiceRuntime.SessionTurnQueue(),
  nudgeExpiryTimer: null,
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  const body = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : await response.blob();
  if (!response.ok) throw new Error(body?.error || `Request failed (${response.status}).`);
  return body;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("visible"), 3400);
}

function setCallState(label, active = false) {
  $("#call-state-text").textContent = label;
  $("#call-state").classList.toggle("active", active);
  $("#transcript-meta").textContent = active ? state.locale : "Waiting to start";
}

function updateStartEnabled() {
  $("#start-call").disabled = !$("#test-persona").checked || state.callActive || state.scenarioBusy;
}

function updateComposer() {
  const enabled = state.callActive;
  $("#message-input").disabled = !enabled;
  $("#send-message").disabled = !enabled;
  $("#locale-select").disabled = enabled;
  $("#export-transcript").disabled = state.transcript.length === 0;
}

function renderMessage(role, text, { status = "", sourceIds = [], time = new Date() } = {}) {
  const transcript = $("#transcript");
  transcript.querySelector(".empty-transcript")?.remove();
  const row = document.createElement("div");
  row.className = `message-row ${role}`;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const bullet = document.createElement("span");
  bullet.className = "speaker-bullet";
  const speaker = document.createElement("span");
  speaker.textContent = role === "customer" ? "CUSTOMER" : "VOICE AGENT";
  const clock = document.createElement("span");
  clock.textContent = time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  meta.append(bullet, speaker, clock);
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  row.append(meta, bubble);
  if (status) {
    const tag = document.createElement("span");
    tag.className = "message-status";
    tag.textContent = `${status.replaceAll("_", " ")}${sourceIds.length ? ` · ${sourceIds.length} source${sourceIds.length > 1 ? "s" : ""}` : ""}`;
    row.append(tag);
  }
  transcript.append(row);
  transcript.scrollTop = transcript.scrollHeight;
  state.transcript.push({ role, text, status, source_ids: sourceIds, created_at: time.toISOString() });
  updateComposer();
}

function showInterim(text) {
  $("#interim-line").textContent = text ? `Listening: ${text}` : "";
}

function renderCitations(citations) {
  const container = $("#citations");
  if (!citations?.length) {
    if (!state.citations.size) container.innerHTML = '<div class="citation-empty">No approved source matched the latest answer.</div>';
    return;
  }
  for (const citation of citations) {
    if (!state.citations.has(citation.record_id)) state.citations.set(citation.record_id, citation);
  }
  container.replaceChildren();
  for (const citation of state.citations.values()) {
    const record = window.kbRecords?.get(citation.record_id);
    const card = document.createElement("div");
    card.className = "citation-card";
    const top = document.createElement("div");
    top.className = "citation-top";
    const title = document.createElement("div");
    title.className = "citation-title";
    title.textContent = citation.title;
    const score = document.createElement("div");
    score.className = "citation-score";
    score.textContent = record?.category || "source";
    top.append(title, score);
    const meta = document.createElement("div");
    meta.className = "citation-meta";
    meta.textContent = `${citation.source_id} · ${citation.source_section} · v${citation.version}`;
    card.append(top, meta);
    if (record?.content) {
      const content = document.createElement("div");
      content.className = "citation-content";
      content.textContent = record.content;
      card.append(content);
    }
    container.append(card);
  }
  $("#citation-count").textContent = String(state.citations.size);
}

function renderNudges(nudges) {
  const container = $("#nudges");
  const now = Date.now();
  const visible = VoiceRuntime.activeNudges(nudges, now);
  clearTimeout(state.nudgeExpiryTimer);
  const nextExpiry = VoiceRuntime.nextNudgeExpiryMs(visible, now);
  state.nudgeExpiryTimer = nextExpiry == null ? null : setTimeout(() => renderNudges(visible), nextExpiry + 1);
  container.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "nudge-empty";
    empty.innerHTML = '<div class="nudge-orbit">◎</div><b>All clear</b><span>No active prompts. The confidence gate and cooldown suppress weak or repeated signals.</span>';
    container.append(empty);
    return;
  }
  for (const nudge of visible) {
    const card = document.createElement("div");
    card.className = `nudge-card ${nudge.priority === "high" ? "high" : ""}`;
    const head = document.createElement("div");
    head.className = "nudge-card-head";
    const priority = document.createElement("span");
    priority.className = "nudge-priority";
    priority.textContent = `${nudge.priority} · ${nudge.signal.replaceAll("_", " ")}`;
    const confidence = document.createElement("span");
    confidence.className = "nudge-confidence";
    confidence.textContent = `${Math.round(nudge.confidence * 100)}%`;
    head.append(priority, confidence);
    const message = document.createElement("div");
    message.className = "nudge-message";
    message.textContent = nudge.message;
    const age = document.createElement("div");
    age.className = "nudge-age";
    age.textContent = `EXPIRES ${new Date(nudge.expires_at).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" })}`;
    card.append(head, message, age);
    container.append(card);
  }
}

function formatLatency(component) {
  if (!component?.count) return "—";
  const p50 = component.p50_ms == null ? "—" : `${Math.round(component.p50_ms)}ms`;
  const p95 = component.p95_ms == null ? "—" : `${Math.round(component.p95_ms)}ms`;
  return `${p50} / ${p95}`;
}

async function refreshMetrics() {
  try {
    const report = await request("/api/metrics");
    $("#latency-asr").textContent = formatLatency(report.components.asr);
    $("#latency-signal").textContent = formatLatency(report.components.signal_extraction);
    $("#latency-e2e").textContent = formatLatency(report.components.end_to_end);
    $("#latency-llm").textContent = "Not used";
    $("#metrics-sample").textContent = report.sample_count
      ? `${report.sample_count} component samples · localhost · client reported / observed`
      : "No live samples collected.";
  } catch (error) {
    $("#metrics-sample").textContent = error.message;
  }
}

async function submitSignal(text, speaker, asrLatency = 0, inputKind = "typed", sessionId = state.sessionId) {
  if (!sessionId || !text.trim()) return null;
  const started = performance.now();
  const result = await request("/api/signals", {
    method: "POST",
    body: JSON.stringify({ call_id: sessionId, locale: state.locale, text, speaker, asr_latency_ms: asrLatency, input_kind: speaker === "agent" ? "generated" : inputKind }),
  });
  if (sessionId !== state.sessionId) return null;
  const responseAt = performance.now();
  renderNudges(result.active);
  const delivery = performance.now() - responseAt;
  const endToEnd = asrLatency + responseAt - started + delivery;
  await request("/api/metrics", {
    method: "POST",
    body: JSON.stringify({ delivery_ms: delivery, end_to_end_ms: endToEnd }),
  });
  refreshMetrics();
  return result;
}

function chooseVoice(locale) {
  const voices = window.speechSynthesis?.getVoices?.() || [];
  const exact = voices.find((voice) => voice.lang.toLowerCase() === locale.toLowerCase());
  if (exact) return exact;
  const prefix = locale.split("-")[0].toLowerCase();
  return voices.find((voice) => voice.lang.toLowerCase().startsWith(`${prefix}-`)) || null;
}

function speak(text, locale, onStart = null) {
  if (!("speechSynthesis" in window) || !text) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = locale;
  utterance.rate = 0.96;
  if (onStart) utterance.onstart = onStart;
  const voice = chooseVoice(locale);
  if (voice) utterance.voice = voice;
  window.speechSynthesis.speak(utterance);
  return true;
}

function markDisclosureDelivered(sessionId) {
  if (sessionId !== state.sessionId || !state.callActive || state.closingRequested) return;
  request("/api/signals", {
    method: "POST",
    body: JSON.stringify({ call_id: sessionId, locale: state.locale, text: "", speaker: "agent", input_kind: "generated", disclosure_delivered: true }),
  }).then((result) => renderNudges(result.active)).catch((error) => toast(`Could not record the disclosure event: ${error.message}`));
}

function sendTurn(message, options = {}) {
  const sessionId = state.sessionId;
  const inputKind = options.inputKind || "typed";
  if (!sessionId || (state.closingRequested && !(inputKind === "speech" && state.acceptingFinalSpeech))) return Promise.resolve();
  return state.turnQueue.enqueue(sessionId, (id) => state.sessionId === id, () => processTurn(message, { ...options, sessionId, inputKind }));
}

async function processTurn(message, { asrLatency = 0, inputKind = "typed", sessionId }) {
  const text = message.trim();
  if (!text || !sessionId || sessionId !== state.sessionId || !state.callActive) return;
  renderMessage("customer", text);
  $("#message-input").value = "";
  $("#send-message").disabled = true;
  showInterim("");
  try {
    const [signalResult, answer] = await Promise.all([
      submitSignal(text, "customer", asrLatency, inputKind, sessionId).catch((error) => ({ error })),
      request("/api/answer", { method: "POST", body: JSON.stringify({ session_id: sessionId, message: text }) }),
    ]);
    if (sessionId !== state.sessionId) return;
    if (signalResult?.error) toast(`Nudge stream: ${signalResult.error.message}`);
    const sourceIds = (answer.citations || []).map((citation) => citation.record_id);
    renderMessage("agent", answer.answer, { status: answer.status, sourceIds });
    renderCitations(answer.citations || []);
    state.lastResult = answer;
    if (answer.business_action) {
      toast(answer.business_action.type === "mock_callback_request"
        ? "Mock callback request recorded in the call result; no real call was scheduled."
        : "Human assistance request captured for the demo.");
    }
    speak(answer.answer, state.locale);
    const agentSignals = await submitSignal(answer.answer, "agent", 0, "generated", sessionId).catch(() => null);
    if (sessionId !== state.sessionId) return;
    if (agentSignals?.active) renderNudges(agentSignals.active);
  } catch (error) {
    toast(error.message);
  } finally {
    $("#send-message").disabled = !state.callActive || state.closingRequested;
  }
}

function makeRecognition(locale) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return null;
  const recognition = new SpeechRecognition();
  recognition.lang = locale;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => {
    $("#mic-status").textContent = `Listening · ${locale}`;
    setCallState("Listening", true);
  };
  recognition.onspeechstart = () => { state.lastSpeechStart = performance.now(); };
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = result[0]?.transcript?.trim();
      if (!text) continue;
      if (result.isFinal) {
        state.speechRecognized = true;
        const speechStart = state.lastSpeechStart;
        const latency = speechStart ? Math.max(0, performance.now() - speechStart) : 0;
        state.lastSpeechStart = 0;
        sendTurn(text, { asrLatency: latency, inputKind: "speech" });
      } else {
        interim += `${text} `;
      }
    }
    showInterim(interim.trim());
  };
  recognition.onerror = (event) => {
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      $("#mic-status").textContent = "Mic permission blocked · use typed fallback";
      toast("Browser microphone permission was blocked. You can still use the typed conversation.");
    } else if (event.error !== "no-speech" && event.error !== "aborted") {
      $("#mic-status").textContent = `Speech service · ${event.error}`;
    }
  };
  recognition.onend = () => {
    if (state.callActive && !state.closingRequested && state.recognition === recognition) {
      try { recognition.start(); } catch { /* The browser may still be finalizing a recognition cycle. */ }
    }
  };
  return recognition;
}

async function createRecorder() {
  if (!$("#record-consent").checked) return false;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    toast("Audio recording is not available in this browser. The call can continue in text mode.");
    return false;
  }
  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported?.(type));
    state.mediaRecorder = mimeType ? new MediaRecorder(state.mediaStream, { mimeType }) : new MediaRecorder(state.mediaStream);
    state.audioChunks = [];
    state.mediaRecorder.ondataavailable = (event) => { if (event.data?.size) state.audioChunks.push(event.data); };
    state.mediaRecorder.start(1000);
    return true;
  } catch (error) {
    toast(`Could not start local recording: ${error.message}`);
    return false;
  }
}

async function startCall() {
  if (!$("#test-persona").checked) {
    toast("Confirm this is a synthetic test-persona call before starting.");
    return;
  }
  $("#start-call").disabled = true;
  state.locale = $("#locale-select").value;
  state.closingRequested = false;
  state.acceptingFinalSpeech = false;
  state.turnQueue = new VoiceRuntime.SessionTurnQueue();
  state.sessionId = null;
  state.callActive = false;
  state.recognition = null;
  state.speechRecognized = false;
  state.lastSpeechStart = 0;
  state.audioChunks = [];
  state.transcript = [];
  state.citations.clear();
  state.lastResult = null;
  document.querySelector("#transcript").replaceChildren();
  $("#citation-count").textContent = "0";
  $("#citations").innerHTML = '<div class="citation-empty">Sources used in this call will show here.</div>';
  $("#export-transcript").disabled = true;
  renderNudges([]);
  window.speechSynthesis?.cancel();
  try {
    const session = await request("/api/session", { method: "POST", body: JSON.stringify({ locale: state.locale }) });
    state.sessionId = session.session_id;
    state.callActive = true;
    state.transcript = [];
    state.citations.clear();
    state.lastResult = null;
    $("#citation-count").textContent = "0";
    $("#citations").innerHTML = '<div class="citation-empty">Sources used in this call will show here.</div>';
    renderMessage("agent", session.greeting, { status: "greeting" });
    const ttsAvailable = speak(session.greeting, state.locale, () => markDisclosureDelivered(session.session_id));
    if (!ttsAvailable) markDisclosureDelivered(session.session_id);
    const recording = await createRecorder();
    state.recognition = makeRecognition(state.locale);
    if (state.recognition) {
      try {
        state.recognition.start();
        if (!recording) $("#mic-status").textContent = `Speech input · ${state.locale}`;
      } catch (error) {
        $("#mic-status").textContent = "Typed fallback available";
        toast(`Could not start browser speech recognition: ${error.message}`);
      }
    } else {
      $("#mic-status").textContent = recording ? "Recording · typed transcript mode" : "Browser ASR unavailable · typed mode";
      toast("This browser does not expose SpeechRecognition. Use the typed fallback for the conversation.");
    }
    $("#end-call").disabled = false;
    setCallState(state.recognition ? "Connecting" : "Typed mode", Boolean(state.recognition));
    updateComposer();
    $("#message-input").focus();
    if (recording) toast("Local audio recording started with your consent.");
  } catch (error) {
    state.callActive = false;
    toast(error.message);
    setCallState("Ready");
    updateStartEnabled();
  }
}

function stopRecognition() {
  const recognition = state.recognition;
  if (!recognition) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => resolve();
    recognition.addEventListener?.("end", finish, { once: true });
    const oldHandler = recognition.onend;
    recognition.onend = (...args) => {
      oldHandler?.(...args);
      finish();
    };
    try { recognition.stop(); } catch { finish(); }
    setTimeout(finish, 1200);
  });
}

function stopRecorder() {
  const recorder = state.mediaRecorder;
  if (!recorder || recorder.state === "inactive") return Promise.resolve();
  return new Promise((resolve) => {
    recorder.addEventListener("stop", resolve, { once: true });
    try { recorder.stop(); } catch { resolve(); }
  });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the local audio recording."));
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.readAsDataURL(blob);
  });
}

async function endCall() {
  if (!state.callActive || state.closingRequested) return;
  state.closingRequested = true;
  state.acceptingFinalSpeech = true;
  $("#end-call").disabled = true;
  $("#message-input").disabled = true;
  $("#send-message").disabled = true;
  $("#mic-status").textContent = "Finishing the current turn…";
  await stopRecognition();
  state.acceptingFinalSpeech = false;
  await state.turnQueue.drain();
  const sessionId = state.sessionId;
  try {
    const closure = await request("/api/close", { method: "POST", body: JSON.stringify({ call_id: sessionId, locale: state.locale, text: "Call ended." }) });
    renderNudges(closure.active);
    if (closure.emitted?.length) {
      toast("A reminder appeared before the call ended.");
      await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    }
  } catch (error) { toast(error.message); }

  state.callActive = false;
  state.recognition = null;
  window.speechSynthesis?.cancel();
  await stopRecorder();
  let saved = false;
  if ($("#record-consent").checked && state.speechRecognized && state.audioChunks.length) {
    try {
      const blob = new Blob(state.audioChunks, { type: state.audioChunks[0]?.type || "audio/webm" });
      const audioBase64 = await blobToBase64(blob);
      const evidence = await request("/api/evidence", {
        method: "POST",
        body: JSON.stringify({
          consent: true,
          test_persona: $("#test-persona").checked,
          locale: state.locale,
          transcript: state.transcript,
          audio_mime: blob.type.split(";")[0] || "audio/webm",
          audio_base64: audioBase64,
          audio_source: "microphone",
          results: { last_status: state.lastResult?.status || "no_turns", source_ids: state.lastResult?.citations?.map((item) => item.record_id) || [] },
        }),
      });
      saved = true;
      toast(`Consent-based recording saved locally · ${evidence.evidence_id.slice(0, 8)}`);
      await loadEvidence();
    } catch (error) {
      toast(`Could not save call evidence: ${error.message}`);
    }
  } else if ($("#record-consent").checked && !state.speechRecognized) {
    toast("No voice input was transcribed, so no audio evidence was saved. Typed fallback remains in this tab.");
  } else {
    toast("Call ended. Nothing was saved because recording consent was off.");
  }
  state.mediaStream?.getTracks().forEach((track) => track.stop());
  state.mediaStream = null;
  state.mediaRecorder = null;
  state.audioChunks = [];
  state.sessionId = null;
  state.closingRequested = false;
  state.acceptingFinalSpeech = false;
  state.turnQueue = new VoiceRuntime.SessionTurnQueue();
  $("#mic-status").textContent = saved ? "Saved with consent" : "Call ended";
  setCallState("Ready");
  updateComposer();
  updateStartEnabled();
  refreshMetrics();
}

function downloadJson(filename, content) {
  const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function runScenarios() {
  if (state.scenarioBusy || state.callActive) return;
  state.scenarioBusy = true;
  updateStartEnabled();
  const container = $("#scenario-results");
  container.classList.remove("hidden");
  container.textContent = "Running scripted test personas…";
  try {
    const { scenarios } = await request("/api/scenarios");
    const rows = [];
    for (const scenario of scenarios) {
      const session = await request("/api/session", { method: "POST", body: JSON.stringify({ locale: scenario.locale }) });
      const answer = await request("/api/answer", { method: "POST", body: JSON.stringify({ session_id: session.session_id, message: scenario.question }) });
      const ids = (answer.citations || []).map((citation) => citation.record_id);
      const correct = answer.status === scenario.expected_status && (scenario.expected_record_id ? ids[0] === scenario.expected_record_id : ids.length === 0);
      rows.push({ id: scenario.id, correct, expected: scenario.expected_status, actual: answer.status });
    }
    const correctCount = rows.filter((row) => row.correct).length;
    container.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "result-heading";
    const title = document.createElement("span");
    title.textContent = "SCRIPTED CALL LOGIC · NO AUDIO";
    const summary = document.createElement("span");
    summary.className = correctCount === rows.length ? "result-pass" : "result-fail";
    summary.textContent = `${correctCount}/${rows.length} passed`;
    heading.append(title, summary);
    container.append(heading);
    for (const row of rows) {
      const element = document.createElement("div");
      element.className = `result-row ${row.correct ? "" : "fail"}`;
      const mark = document.createElement("span");
      mark.className = "result-mark";
      mark.textContent = row.correct ? "✓" : "!";
      const label = document.createElement("span");
      label.textContent = row.id.replaceAll("_", " ");
      const details = document.createElement("span");
      details.className = "result-detail";
      details.textContent = row.correct ? row.actual : `${row.actual} / expected ${row.expected}`;
      element.append(mark, label, details);
      container.append(element);
    }
    toast(correctCount === rows.length ? "Scripted call checks passed. These are logic checks, not recorded calls." : "Some scripted call checks need review.");
  } catch (error) {
    container.textContent = error.message;
  } finally {
    state.scenarioBusy = false;
    updateStartEnabled();
  }
}

async function runRetrieval() {
  const container = $("#retrieval-results");
  container.classList.remove("hidden");
  container.textContent = "Evaluating labeled questions…";
  $("#run-retrieval").disabled = true;
  try {
    const report = await request("/api/evaluation");
    const counts = report.verdict_counts || {};
    const correct = counts.correct || 0;
    const rows = report.results.map((item) => ({ question: item.question, verdict: item.verdict, category: item.category }));
    container.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "result-heading";
    const title = document.createElement("span");
    title.textContent = `${report.total_queries} LABELED QUERIES · SYNTHETIC CORPUS`;
    const summary = document.createElement("span");
    summary.className = counts.incorrect ? "result-fail" : "result-pass";
    summary.textContent = `${correct}/${report.total_queries} correct`;
    heading.append(title, summary);
    container.append(heading);
    for (const row of rows) {
      const element = document.createElement("div");
      element.className = `result-row ${row.verdict === "correct" ? "" : "fail"}`;
      const mark = document.createElement("span");
      mark.className = "result-mark";
      mark.textContent = row.verdict === "correct" ? "✓" : "!";
      const label = document.createElement("span");
      label.textContent = row.question;
      const details = document.createElement("span");
      details.className = "result-detail";
      details.textContent = `${row.category} · ${row.verdict}`;
      element.append(mark, label, details);
      container.append(element);
    }
    toast(`Retrieval result: ${correct}/${report.total_queries} correct. Details saved under evidence/.`);
  } catch (error) {
    container.textContent = error.message;
  } finally {
    $("#run-retrieval").disabled = false;
  }
}

async function runNudgeChecks() {
  const button = $("#run-nudge-checks");
  button.disabled = true;
  const scenarios = [
    ["compliance_gap", "Your policy is guaranteed to cover everything with no exclusions."],
    ["missed_cross_sell", "I have another policy and want to hear about a rider."],
    ["rising_frustration", "The premium is too expensive and I feel frustrated."],
    ["payment_difficulty", "I am having trouble paying the installment this month."],
  ];
  let passes = 0;
  try {
    for (const [expected, text] of scenarios) {
      const result = await request("/api/signals", {
        method: "POST",
        body: JSON.stringify({ call_id: `logic-check-${crypto.randomUUID()}`, locale: $("#locale-select").value, text, speaker: "customer" }),
      });
      if (result.emitted.some((item) => item.signal === expected)) passes += 1;
    }
    const noisy = await request("/api/signals", {
      method: "POST",
      body: JSON.stringify({ call_id: `logic-check-${crypto.randomUUID()}`, locale: $("#locale-select").value, text: "um maybe background noise, not sure", speaker: "customer" }),
    });
    if (!noisy.emitted.length) passes += 1;
    toast(`Nudge logic: ${passes}/5 checks passed. Text-only checks; no audio was analyzed.`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderLocalization(examples) {
  const container = $("#localization-examples");
  container.replaceChildren();
  for (const [market, items] of Object.entries(examples)) {
    for (const item of items) {
      const card = document.createElement("div");
      card.className = "localization-card";
      const label = document.createElement("div");
      label.className = "localization-card-label";
      label.textContent = `${market} · ${item.intent}`;
      const quote = document.createElement("blockquote");
      quote.textContent = `“${item.localized}”`;
      const reason = document.createElement("small");
      reason.textContent = item.reason;
      card.append(label, quote, reason);
      container.append(card);
    }
  }
}

function renderEvidence(calls) {
  const container = $("#saved-evidence");
  container.replaceChildren();
  if (!calls?.length) return;
  const heading = document.createElement("div");
  heading.className = "saved-heading";
  heading.textContent = `CONSENTED LOCAL RECORDINGS · ${calls.length}`;
  container.append(heading);
  for (const call of calls.slice(0, 10)) {
    const row = document.createElement("div");
    row.className = "saved-call";
    const summary = document.createElement("div");
    const title = document.createElement("div");
    title.className = "saved-call-title";
    title.textContent = `${call.locale} · ${call.evidence_id.slice(0, 8)}`;
    const meta = document.createElement("div");
    meta.className = "saved-call-meta";
    meta.textContent = `${new Date(call.created_at).toLocaleString()} · ${call.transcript.length} turns · microphone audio only`;
    summary.append(title, meta);
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = `/api/evidence/${call.evidence_id}/audio`;
    row.append(summary, audio);
    container.append(row);
  }
}

async function loadEvidence() {
  try {
    const result = await request("/api/evidence");
    renderEvidence(result.calls);
  } catch { /* Evidence list is an optional local feature. */ }
}

async function showHealth() {
  try {
    const data = await request("/api/health");
    const details = $("#health-details");
    details.replaceChildren();
    const values = [
      ["Speech input", `${data.stt.provider} · ${data.stt.model}`],
      ["Speech output", `${data.tts.provider} · installed voices`],
      ["Retrieval", `${data.retrieval.mode} · ${data.retrieval.records} records`],
      ["Embeddings", data.retrieval.embeddings_configured ? "configured" : "not configured"],
      ["Language model", data.llm.mode],
      ["Recording", "local storage · explicit consent required"],
    ];
    for (const [label, value] of values) {
      const row = document.createElement("div");
      row.className = "health-row";
      const key = document.createElement("span");
      key.textContent = label;
      const content = document.createElement("b");
      content.textContent = value;
      row.append(key, content);
      details.append(row);
    }
    $("#health-dialog").showModal();
  } catch (error) { toast(error.message); }
}

async function loadInitialData() {
  try {
    const [kb, localization] = await Promise.all([request("/api/kb/records"), request("/api/localization")]);
    window.kbRecords = new Map(kb.records.map((record) => [record.record_id, record]));
    renderLocalization(localization);
  } catch (error) {
    toast(`Could not load demo data: ${error.message}`);
  }
  await Promise.all([loadEvidence(), refreshMetrics()]);
}

$("#test-persona").addEventListener("change", updateStartEnabled);
$("#start-call").addEventListener("click", startCall);
$("#end-call").addEventListener("click", endCall);
$("#message-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendTurn($("#message-input").value);
});
$("#run-scenarios").addEventListener("click", runScenarios);
$("#run-retrieval").addEventListener("click", runRetrieval);
$("#run-nudge-checks").addEventListener("click", runNudgeChecks);
$("#refresh-metrics").addEventListener("click", refreshMetrics);
$("#health-button").addEventListener("click", showHealth);
$("#export-transcript").addEventListener("click", () => {
  if (!state.transcript.length) return;
  const safeTranscript = state.transcript.map((entry) => ({ ...entry, text: VoiceRuntime.redactPii(entry.text) }));
  downloadJson(`voice-lab-${state.locale}-${new Date().toISOString().slice(0, 10)}.json`, {
    locale: state.locale,
    type: "synthetic demo transcript; not a recording; text PII redacted",
    transcript: safeTranscript,
  });
});

loadInitialData();
setInterval(refreshMetrics, 5000);
