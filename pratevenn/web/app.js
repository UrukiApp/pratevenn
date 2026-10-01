const $ = (id) => document.getElementById(id);
const welcomeTemplate = $("welcome")?.cloneNode(true);
function showWelcome() {
  if (welcomeTemplate) $("messages").replaceChildren(welcomeTemplate.cloneNode(true));
}
let socket, context, stream, capture, input;
let session = 0, voiceMode = false, busy = false, finished = false, playbackEnd = 0, initialized = false;
let replyNode, sources = new Set(), state = "idle", listenAfter = 0;
let frames = [], preRoll = [], speaking = false, voiced = 0, quiet = 0, duration = 0;
let noiseFloor = 0.005, hpPrevIn = 0, hpPrevOut = 0, audibleBurst = 0;
let analyser, holoFrame;
const holoSamples = new Uint8Array(256);
let chatId = crypto.randomUUID(), chat = [], restoreHistory, pendingUser, pendingSource;
let findings = [], dismissed = new Set(), reviewedTurns = 0, contextOmitted = false;
let archiveChain = Promise.resolve(), maxChatMessages = 400;
const categoryLabels = {
  word_order: "Word order", verb_form: "Verb forms", noun_form: "Noun forms",
  agreement: "Agreement", preposition: "Prepositions", spelling: "Spelling", other: "Other",
};

function reviewControls() {
  $("reviewChat").disabled = !initialized || busy || !chat.length;
}

async function archiveRequest(path = "", method = "GET", messages) {
  const response = await fetch(`/api/chats${path}`, {
    method, headers: { "X-Pratevenn-Request": "1", "Content-Type": "application/json" },
    body: messages === undefined ? undefined : JSON.stringify(messages),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Could not access saved chats.");
  return result;
}

async function listChats() {
  const chats = await archiveRequest();
  const selected = $("savedChats").value;
  $("savedChats").replaceChildren(new Option("Choose a chat", ""));
  for (const item of chats) {
    $("savedChats").add(new Option(`${item.title} · ${new Date(item.updated).toLocaleDateString()}`, item.id));
  }
  $("savedChats").value = chats.some((item) => item.id === selected) ? selected : "";
  $("savedChats").onchange();
  $("deleteAllChats").disabled = !chats.length;
}

function archiveOperation(operation) {
  archiveChain = archiveChain.catch(() => {}).then(operation).catch((error) => {
    $("historyStatus").textContent = error.message;
  });
  return archiveChain;
}

function saveChat() {
  if (!$("saveChats").checked || !chat.length) return;
  const identifier = chatId, messages = chat.slice();
  archiveOperation(async () => {
    if (!$("saveChats").checked) return;
    await archiveRequest(`/${identifier}`, "PUT", messages);
    $("historyStatus").textContent = "Completed turns saved on this computer.";
    await listChats();
  });
}

function renderReview() {
  $("patterns").replaceChildren();
  $("suggestions").replaceChildren();
  const visible = findings.filter((item) => !dismissed.has(JSON.stringify(item)));
  for (const [category, label] of Object.entries(categoryLabels)) {
    const turns = new Set(visible.filter((item) => item.category === category).map((item) => item.turn));
    if (turns.size < 3) continue;
    const pattern = document.createElement("p");
    pattern.textContent = `${label} appeared in ${turns.size} separate turns.`;
    $("patterns").append(pattern);
  }
  for (const item of visible) {
    const article = document.createElement("article");
    article.className = "suggestion";
    const title = document.createElement("p");
    title.className = "suggestion-title";
    title.textContent = `Turn ${item.turn} · ${categoryLabels[item.category] || "Suggestion"}${chat[(item.turn - 1) * 2]?.source === "audio" ? " · Speech transcript" : ""}`;
    const correction = document.createElement("p");
    correction.lang = "nb";
    correction.textContent = `${item.original} → ${item.correction}`;
    const explanation = document.createElement("p");
    explanation.lang = "nb";
    explanation.textContent = item.explanation;
    const dismiss = document.createElement("button");
    dismiss.type = "button"; dismiss.className = "secondary";
    dismiss.textContent = "Dismiss suggestion";
    dismiss.onclick = () => {
      dismissed.add(JSON.stringify(item));
      renderReview();
      const next = $("suggestions").querySelector("button");
      if (next) next.focus();
      else $("reviewChat").focus();
    };
    article.append(title, correction, explanation, dismiss);
    $("suggestions").append(article);
  }
  $("reviewStatus").textContent = reviewedTurns
    ? `${reviewedTurns} turns reviewed. ${visible.length} suggestions. Patterns require at least three separate turns.`
    : "Complete a turn, then review your practice.";
}

function resetContext() {
  contextOmitted = false;
  $("contextUsage").value = 0;
  $("contextStatus").textContent = "Start a turn to measure context usage.";
}

function newChat() {
  stop();
  chatId = crypto.randomUUID(); chat = []; findings = []; dismissed.clear(); reviewedTurns = 0;
  showWelcome();
  $("text").value = "";
  $("savedChats").value = ""; $("savedChats").onchange();
  $("latency").textContent = ""; $("historyStatus").textContent = "";
  renderReview(); reviewControls();
  status("New chat. Ready when you are.", "idle");
}

function animateHologram() {
  holoFrame = undefined;
  let level = 0;
  if (sources.size && state === "speaking") {
    analyser.getByteTimeDomainData(holoSamples);
    level = Math.min(1, Math.sqrt(holoSamples.reduce((sum, sample) => sum + ((sample - 128) / 128) ** 2, 0) / holoSamples.length) * 5);
    holoFrame = requestAnimationFrame(animateHologram);
  }
  $("hologram").style.setProperty("--voice", level);
}

function status(message, nextState = state) {
  state = nextState;
  document.body.dataset.state = state;
  $("status").textContent = message;
}

function resetCapture() {
  frames = []; preRoll = []; speaking = false; voiced = 0; quiet = 0; duration = 0;
  audibleBurst = 0; hpPrevIn = 0; hpPrevOut = 0;
  $("meter").value = 0;
}

function microphone(enabled) {
  stream?.getAudioTracks().forEach((track) => { track.enabled = enabled; });
  resetCapture();
}

function message(role, text = "") {
  $("welcome")?.remove();
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "user" ? "YOU" : "PRATEVENN";
  const content = document.createElement("p");
  content.className = "content";
  content.lang = "nb";
  content.textContent = text;
  article.append(who, content);
  $("messages").append(article);
  $("messages").scrollTop = $("messages").scrollHeight;
  return content;
}

function renderInlineFeedback(article, findings) {
  if (!article || !findings || !findings.length) return;
  if (article.querySelector(".inline-feedback")) return;
  const container = document.createElement("div");
  container.className = "inline-feedback";
  for (const item of findings) {
    const box = document.createElement("div");
    box.className = "feedback-item";
    const header = document.createElement("div");
    header.className = "feedback-header";
    const cat = document.createElement("span");
    cat.className = "feedback-category";
    cat.textContent = categoryLabels[item.category] || "Tip";
    header.append(cat);
    const diff = document.createElement("p");
    diff.className = "feedback-diff";
    diff.lang = "nb";
    diff.textContent = `${item.original} → ${item.correction}`;
    const exp = document.createElement("p");
    exp.className = "feedback-exp";
    exp.lang = "nb";
    exp.textContent = item.explanation;
    box.append(header, diff, exp);
    container.append(box);
  }
  article.append(container);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function readyForTurn() {
  if (!finished || sources.size) return;
  busy = false;
  reviewControls();
  $("send").disabled = false;
  $("start").disabled = voiceMode;
  if (voiceMode) {
    microphone(true);
    listenAfter = performance.now() + 350;
    status("Listening — take your time", "listening");
  } else status("Ready when you are", "idle");
  if (chat.length >= maxChatMessages) {
    stop();
    const maxTurns = Math.floor(maxChatMessages / 2);
    status(`This chat has reached ${maxTurns} turns. Choose New chat to keep practicing.`, "idle");
  }
}

function stop() {
  session += 1;
  voiceMode = false; busy = false; finished = false;
  restoreHistory = undefined; pendingUser = undefined; replyNode = undefined;
  socket?.close(); socket = undefined;
  stream?.getTracks().forEach((track) => track.stop()); stream = undefined;
  capture?.disconnect(); input?.disconnect(); capture = undefined; input = undefined;
  for (const source of sources) { source.onended = null; source.stop(); source.disconnect(); }
  sources.clear();
  cancelAnimationFrame(holoFrame); holoFrame = undefined;
  $("hologram").style.setProperty("--voice", 0);
  playbackEnd = 0;
  resetCapture();
  noiseFloor = 0.005;
  $("start").disabled = !initialized; $("stop").disabled = true; $("send").disabled = !initialized;
  $("modelSettings").disabled = !initialized;
  if ($("contextSize")) $("contextSize").disabled = !initialized;
  $("latency").textContent = "";
  resetContext(); reviewControls();
  status("Session ended. Start again whenever you like.", "idle");
}

function fail(error) {
  stop();
  if (!initialized) $("ready").textContent = "Unavailable";
  status(error.message || String(error), "error");
}

async function audioContext() {
  context ??= new AudioContext();
  if (!analyser) {
    analyser = context.createAnalyser();
    analyser.fftSize = holoSamples.length;
    analyser.connect(context.destination);
  }
  await context.resume();
}

async function handle(event, generation) {
  if (generation !== session) return;
  if (event.llm_device) $("ready").textContent = `${event.llm_device} · Models ready`;
  if (event.type === "status") status(event.message, "processing");
  if (event.type === "transcript") {
    pendingUser = { role: "user", content: event.text, source: pendingSource };
    message("user", event.text);
    replyNode = message("assistant");
  }
  if (event.type === "context") {
    const percentage = Math.round(event.tokens / event.capacity * 100);
    contextOmitted ||= event.omitted > 0;
    $("contextUsage").value = percentage;
    $("contextStatus").textContent = `${percentage}% used · ${event.tokens.toLocaleString()} / ${event.capacity.toLocaleString()} tokens · ${event.messages} messages.${contextOmitted ? " Earlier messages have left the model's context." : ""}`;
  }
  if (event.type === "review") {
    findings = event.findings; reviewedTurns = event.turns;
    renderReview();
    $("reviewDetails").open = true;
  }
  if (event.type === "text" && replyNode) {
    replyNode.textContent += event.text;
    $("messages").scrollTop = $("messages").scrollHeight;
  }
  if (event.type === "feedback" && event.findings?.length) {
    const userArticles = $("messages").querySelectorAll("article.message.user");
    renderInlineFeedback(userArticles[userArticles.length - 1], event.findings);
  }
  if (event.type === "audio") {
    const bytes = Uint8Array.from(atob(event.data), (character) => character.charCodeAt(0));
    const buffer = await context.decodeAudioData(bytes.buffer);
    if (generation !== session) return;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    sources.add(source);
    source.onended = () => {
      sources.delete(source);
      source.disconnect();
      if (generation === session) readyForTurn();
    };
    playbackEnd = Math.max(context.currentTime + 0.03, playbackEnd);
    source.start(playbackEnd);
    playbackEnd += buffer.duration;
    status("Pratevenn is speaking …", "speaking");
    if (holoFrame === undefined) animateHologram();
  }
  if (event.type === "done") {
    if (event.feedback?.length) {
      const userArticles = $("messages").querySelectorAll("article.message.user");
      renderInlineFeedback(userArticles[userArticles.length - 1], event.feedback);
    }
    if (!event.review && !event.empty && pendingUser && replyNode?.textContent.trim()) {
      chat.push(pendingUser, { role: "assistant", content: replyNode.textContent.trim(), source: "text" });
      pendingUser = undefined;
      saveChat();
      if (reviewedTurns) $("reviewStatus").textContent = "New turns are available. Review again to include them.";
    }
    finished = true;
    if (event.first_audio_seconds) $("latency").textContent = `${event.first_audio_seconds}s to first audio`;
    readyForTurn();
  }
  if (event.type === "error") fail(new Error(event.message));
}

async function connect(generation) {
  if (generation !== session) return;
  if (socket?.readyState === WebSocket.OPEN) return;
  $("modelSettings").disabled = true;
  if ($("contextSize")) $("contextSize").disabled = true;
  const connection = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  socket = connection;
  let chain = Promise.resolve();
  await new Promise((resolve, reject) => {
    connection.onopen = resolve;
    connection.onerror = () => reject(new Error("Could not connect to Pratevenn. Is the server running?"));
    connection.onclose = () => reject(new Error("Connection ended. Start again to reconnect."));
  });
  if (generation !== session) { connection.close(); return; }
  restoreHistory = chat.length ? chat.slice() : undefined;
  connection.onmessage = (event) => {
    chain = chain.then(() => handle(JSON.parse(event.data), generation)).catch((error) => {
      if (generation === session) fail(error);
    });
  };
  connection.onclose = () => {
    if (generation === session) fail(new Error("Connection ended. Start again to reconnect."));
  };
  $("stop").disabled = false;
}

function submit(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("Pratevenn is disconnected.");
  const maxTurns = Math.floor(maxChatMessages / 2);
  if (payload.type !== "review" && chat.length >= maxChatMessages) {
    throw new Error(`This chat has reached ${maxTurns} turns. Choose New chat to keep practicing.`);
  }
  if (payload.type !== "review" && (!$("systemPrompt").value.trim() || !$("systemPrompt").checkValidity())) {
    throw new Error(`Please enter a system prompt of 1 to ${$("systemPrompt").maxLength} characters.`);
  }
  busy = true; finished = false; replyNode = undefined;
  pendingSource = payload.type; pendingUser = undefined;
  reviewControls();
  microphone(false);
  $("send").disabled = true; $("start").disabled = true;
  $("latency").textContent = "";
  status(payload.type === "review" ? "Reviewing your practice …" : "Understanding …", "processing");
  const models = { stt: $("sttModel").value, llm: $("llmModel").value, tts: $("ttsModel").value };
  const contextSize = parseInt($("contextSize")?.value, 10);
  const speed = Number($("speed")?.value || 0.85);
  socket.send(JSON.stringify({ ...payload, models, ...(contextSize ? { context_size: contextSize } : {}), ...(restoreHistory ? { history: restoreHistory } : {}),
    system_prompt: $("systemPrompt").value, feedback: Boolean($("showInlineFeedback")?.checked), speed }));
  restoreHistory = undefined;
}

async function sendRecording(recorded, generation) {
  busy = true; microphone(false);
  status("Understanding …", "processing");
  const count = recorded.reduce((total, frame) => total + frame.length, 0);
  const outputLength = Math.min(480000, Math.floor(count * 16000 / context.sampleRate));
  const offline = new OfflineAudioContext(1, outputLength, 16000);
  const buffer = offline.createBuffer(1, count, context.sampleRate);
  let offset = 0;
  for (const frame of recorded) { buffer.copyToChannel(frame, 0, offset); offset += frame.length; }
  const source = offline.createBufferSource();
  source.buffer = buffer; source.connect(offline.destination); source.start();
  const samples = (await offline.startRendering()).getChannelData(0);
  if (generation !== session) return;
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  function word(at, value) { [...value].forEach((character, index) => view.setUint8(at + index, character.charCodeAt(0))); }
  word(0, "RIFF"); view.setUint32(4, bytes.length - 8, true); word(8, "WAVE"); word(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  word(36, "data"); view.setUint32(40, bytes.length - 44, true);
  for (let index = 0; index < samples.length; index++) {
    view.setInt16(44 + index * 2, Math.max(-1, Math.min(1, samples[index])) * 32767, true);
  }
  let binary = "";
  for (let index = 0; index < bytes.length; index += 8192) binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
  submit({ type: "audio", data: btoa(binary) });
}

function captureFrame(samples) {
  if (!voiceMode || busy || state !== "listening" || performance.now() < listenAfter) return;
  const seconds = samples.length / context.sampleRate;
  const r = Math.exp(-500 / context.sampleRate);
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const x = samples[i];
    const y = x - hpPrevIn + r * hpPrevOut;
    hpPrevIn = x;
    hpPrevOut = y;
    sum += y * y;
  }
  const rms = Math.sqrt(sum / samples.length);
  $("meter").value = rms;

  if (!speaking) {
    if (rms < noiseFloor) {
      noiseFloor = noiseFloor * 0.85 + rms * 0.15;
    } else {
      noiseFloor = noiseFloor * 0.992 + rms * 0.008;
    }
    noiseFloor = Math.max(0.001, Math.min(0.05, noiseFloor));
  }

  const userThreshold = 10 ** (Number($("threshold").value) / 20);
  const effectiveThreshold = Math.max(userThreshold, noiseFloor * 1.8);
  const audible = rms > effectiveThreshold;

  if (!speaking) {
    preRoll.push(samples);
    while (preRoll.length * seconds > 0.35) preRoll.shift();
    voiced = audible ? voiced + seconds : Math.max(0, voiced - seconds * 0.5);
    if (voiced < 0.12) return;
    frames = preRoll; preRoll = []; speaking = true;
    audibleBurst = 0;
    duration = frames.length * seconds;
    $("status").textContent = "I hear you …";
  } else {
    frames.push(samples);
    duration += seconds;
  }

  if (audible) {
    audibleBurst += seconds;
    if (quiet === 0 || audibleBurst >= 0.08) {
      quiet = 0;
    }
  } else {
    audibleBurst = 0;
    quiet += seconds;
  }

  if (quiet * 1000 >= Number($("pause").value) || duration >= 29.5) {
    const trailingSilenceFrames = Math.max(0, Math.floor((quiet - 0.35) / seconds));
    const recorded = trailingSilenceFrames > 0
      ? frames.slice(0, Math.max(1, frames.length - trailingSilenceFrames))
      : frames;
    const generation = session;
    sendRecording(recorded, generation).catch((error) => { if (generation === session) fail(error); });
  }
}

$("start").onclick = async () => {
  if (!initialized || busy) return;
  const generation = session;
  busy = true;
  reviewControls();
  $("send").disabled = true;
  $("start").disabled = true; $("stop").disabled = false;
  status("Allow your microphone to begin …", "idle");
  try {
    await audioContext();
    await connect(generation);
    if (generation !== session) return;
    if (!navigator.mediaDevices) throw new Error("Microphone access needs localhost or HTTPS.");
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    } });
    if (generation !== session) { acquired.getTracks().forEach((track) => track.stop()); return; }
    stream = acquired;
    await context.audioWorklet.addModule("/static/capture.js");
    if (generation !== session) return;
    input = context.createMediaStreamSource(stream);
    capture = new AudioWorkletNode(context, "capture");
    capture.port.onmessage = (event) => captureFrame(event.data);
    input.connect(capture); capture.connect(context.destination);
    stream.getAudioTracks()[0].onended = () => { if (generation === session) fail(new Error("Microphone disconnected. Start again to reconnect.")); };
    voiceMode = true; finished = true;
    readyForTurn();
  } catch (error) {
    if (generation === session) fail(new Error(error.name === "NotAllowedError"
      ? "Microphone access was denied. Allow it in your browser, or type below." : error.message));
  }
};
$("stop").onclick = stop;
$("textForm").onsubmit = async (event) => {
  event.preventDefault();
  const text = $("text").value.trim(), generation = session;
  if (!initialized || !text || busy) return;
  busy = true;
  reviewControls();
  $("send").disabled = true; $("start").disabled = true;
  $("stop").disabled = false;
  microphone(false);
  try {
    await audioContext(); await connect(generation);
    if (generation !== session) return;
    submit({ type: "text", text });
    $("text").value = "";
  } catch (error) { if (generation === session) fail(error); }
};
$("pause").oninput = () => { $("pauseValue").textContent = `${(Number($("pause").value) / 1000).toFixed(1)} s`; };
$("threshold").oninput = () => { $("thresholdValue").textContent = `${$("threshold").value.replace("-", "−")} dB`; };
$("speed").oninput = () => {
  const val = Number($("speed").value);
  $("speedValue").textContent = `${val.toFixed(2).replace(/0$/, "")}x`;
  try { localStorage.setItem("pratevenn.speed", $("speed").value); } catch {}
};
$("newChat").onclick = newChat;
$("savedChats").onchange = () => {
  $("openChat").disabled = $("deleteChat").disabled = !$("savedChats").value;
};
$("saveChats").onchange = () => {
  try { localStorage.setItem("pratevenn.saveChats", String($("saveChats").checked)); } catch {}
  $("historyStatus").textContent = $("saveChats").checked
    ? "Completed text turns will be saved, including this chat." : "New turns will not be saved. Existing saved chats are kept.";
  saveChat();
};
$("showInlineFeedback").onchange = () => {
  try { localStorage.setItem("pratevenn.showInlineFeedback", String($("showInlineFeedback").checked)); } catch {}
  document.body.classList.toggle("hide-inline-feedback", !$("showInlineFeedback").checked);
};
$("openChat").onclick = () => {
  const identifier = $("savedChats").value;
  if (!identifier) return;
  stop();
  $("latency").textContent = "";
  const generation = session;
  busy = true;
  reviewControls();
  $("send").disabled = true; $("start").disabled = true; $("stop").disabled = false;
  $("modelSettings").disabled = true;
  if ($("contextSize")) $("contextSize").disabled = true;
  archiveOperation(async () => {
    if (generation !== session) return;
    try {
      const saved = await archiveRequest(`/${identifier}`);
      if (generation !== session) return;
      chatId = saved.id; chat = saved.messages; restoreHistory = chat.slice();
      findings = []; dismissed.clear(); reviewedTurns = 0;
      $("messages").replaceChildren();
      for (const item of chat) message(item.role, item.content);
      renderReview();
      status("Saved chat reopened. The model will use recent turns that fit.", "idle");
    } finally {
      if (generation === session) {
        busy = false;
        $("send").disabled = $("start").disabled = $("modelSettings").disabled = !initialized;
        if ($("contextSize")) $("contextSize").disabled = !initialized;
        $("stop").disabled = true;
        reviewControls();
      }
    }
  });
};
$("deleteChat").onclick = () => {
  const identifier = $("savedChats").value;
  if (!identifier || !confirm("Delete this saved chat from this computer?")) return;
  if (identifier === chatId) newChat();
  archiveOperation(async () => {
    await archiveRequest(`/${identifier}`, "DELETE");
    await listChats();
    $("historyStatus").textContent = "Saved chat deleted.";
  });
};
$("deleteAllChats").onclick = () => {
  if (!confirm("Delete all saved chats from this computer?")) return;
  newChat();
  $("saveChats").checked = false; $("saveChats").onchange();
  archiveOperation(async () => {
    await archiveRequest("", "DELETE");
    await listChats();
    $("historyStatus").textContent = "All saved chats deleted. Saving is off.";
  });
};
$("reviewChat").onclick = async () => {
  if (!initialized || busy || !chat.length) return;
  const generation = session;
  busy = true; microphone(false); reviewControls();
  $("send").disabled = true; $("start").disabled = true; $("stop").disabled = false;
  try {
    await connect(generation);
    if (generation !== session) return;
    submit({ type: "review", messages: chat });
  } catch (error) { if (generation === session) fail(error); }
};
try { $("saveChats").checked = (localStorage.getItem("pratevenn.saveChats") ?? localStorage.getItem("konvers.saveChats")) === "true"; } catch {}
try {
  const savedFeedback = localStorage.getItem("pratevenn.showInlineFeedback") ?? localStorage.getItem("konvers.showInlineFeedback");
  if (savedFeedback !== null) $("showInlineFeedback").checked = savedFeedback === "true";
} catch {}
try {
  const savedSpeed = localStorage.getItem("pratevenn.speed");
  if (savedSpeed !== null && !isNaN(Number(savedSpeed))) {
    $("speed").value = savedSpeed;
    $("speed").oninput();
  }
} catch {}
document.body.classList.toggle("hide-inline-feedback", !$("showInlineFeedback").checked);

function getEffectiveTheme() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "dark" || explicit === "light") return explicit;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function updateThemeToggleAria(theme) {
  const toggle = $("themeToggle");
  if (!toggle) return;
  const nextTheme = theme === "dark" ? "light" : "dark";
  const label = nextTheme === "dark" ? "Switch to dark theme" : "Switch to light theme";
  toggle.setAttribute("aria-label", label);
  toggle.setAttribute("title", label);
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("pratevenn.theme", theme); } catch {}
  updateThemeToggleAria(theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#212121" : "#ffffff";
}

$("themeToggle")?.addEventListener("click", () => {
  const current = getEffectiveTheme();
  setTheme(current === "dark" ? "light" : "dark");
});

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const theme = getEffectiveTheme();
    updateThemeToggleAria(theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "dark" ? "#212121" : "#ffffff";
  });
}
updateThemeToggleAria(getEffectiveTheme());

$("sidebarToggle")?.addEventListener("click", () => {
  const collapsed = document.body.classList.toggle("sidebar-collapsed");
  $("sidebarToggle").setAttribute("aria-expanded", String(!collapsed));
});
$("sidebarClose")?.addEventListener("click", () => {
  document.body.classList.remove("sidebar-open");
  document.body.classList.add("sidebar-collapsed");
});
$("sidebarNewChat")?.addEventListener("click", () => {
  newChat();
  document.body.classList.remove("sidebar-open");
});
document.addEventListener("click", (event) => {
  const chip = event.target.closest(".prompt-chip");
  if (chip && chip.dataset.prompt) {
    const textInput = $("text");
    if (textInput) {
      textInput.value = chip.dataset.prompt;
      textInput.focus();
    }
  }
});
archiveOperation(listChats);
renderReview();
window.addEventListener("pagehide", stop);
fetch("/api/status").then((response) => {
  if (!response.ok) throw new Error("The local server is not ready.");
  return response.json();
}).then((info) => {
  initialized = true;
  maxChatMessages = info.max_chat_messages;
  $("systemPrompt").value = info.system_prompt;
  $("systemPrompt").maxLength = info.max_system_prompt_chars;
  $("promptSettings").disabled = false;
  $("resetPrompt").onclick = () => { $("systemPrompt").value = info.system_prompt; };
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("pratevenn.models") ?? localStorage.getItem("konvers.models")) || {}; } catch {}
  for (const [kind, choices] of Object.entries(info.models)) {
    const selector = $(`${kind}Model`);
    for (const choice of choices) selector.add(new Option(choice.label, choice.id));
    selector.value = choices.some((choice) => choice.id === saved[kind]) ? saved[kind] : info.selected[kind];
  }
  $("modelSettings").disabled = busy;
  $("modelSettings").onchange = () => {
    const chosen = { stt: $("sttModel").value, llm: $("llmModel").value, tts: $("ttsModel").value };
    try { localStorage.setItem("pratevenn.models", JSON.stringify(chosen)); } catch {}
  };
  const contextSizes = info.context_sizes ?? [2048, 4096, 8192, 16384, 32768];
  const contextSelector = $("contextSize");
  if (contextSelector) {
    let savedContext = null;
    try {
      const stored = localStorage.getItem("pratevenn.contextSize") ?? localStorage.getItem("konvers.contextSize");
      if (stored) savedContext = parseInt(stored, 10);
    } catch {}
    contextSelector.innerHTML = "";
    for (const size of contextSizes) {
      contextSelector.add(new Option(`${size.toLocaleString()} tokens`, String(size)));
    }
    const defaultSize = contextSizes.includes(savedContext) ? savedContext : (info.context_size ?? 8192);
    contextSelector.value = String(defaultSize);
    contextSelector.disabled = busy;
    contextSelector.onchange = () => {
      try { localStorage.setItem("pratevenn.contextSize", contextSelector.value); } catch {}
      resetContext();
    };
  }
  $("ready").textContent = `${info.llm_device} · Models ready`;
  $("start").disabled = busy; $("send").disabled = busy;
  reviewControls();
  if (!busy) status("Ready when you are", "idle");
}).catch(fail);
