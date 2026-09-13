/* The engine owns scores, deadlines and transitions. This file only presents them. */
(() => {
  "use strict";
  const app = document.getElementById("app");
  const cameraPhases = new Set(["preparing", "countdown", "round", "round_result", "recovery"]);
  let state = { phase: "boot", round_seconds: 60, current_round: 1, total_rounds: 10,
    scores: { red: 0, blue: 0 }, clue: "", recaps: [], countdown_remaining: 0,
    round_remaining: 60, result: null, paused: false, camera_ready: false };
  let receivedAt = performance.now();
  let received = false;
  let connected = false;
  let soundBaseline = true;
  let lastCountdown = null;
  let lastRoundSecond = null;
  let lastResultRound = null;
  let recapSignature = "";
  let cameraNeedsRefresh = false;
  let cameraRetryAt = 0;
  let recognition = { red: null, blue: null };
  let recognitionAt = 0;
  const recognitionLifetime = 1500;
  const sounds = Object.fromEntries(["ui-confirm", "countdown-tick", "hunt-start", "object-found", "time-up", "match-complete"].map(name => {
    const audio = new Audio(`audio/${name}_elevenlabs.io.wav`);
    audio.preload = "auto";
    audio.volume = 0.65;
    return [name, audio];
  }));
  function play(name, volume = 0.65) {
    const audio = sounds[name];
    audio.volume = volume;
    audio.currentTime = 0;
    const pending = audio.play();
    if (pending && pending.catch) pending.catch(() => {}); // Browsers may require a first click.
  }
  function stopSounds() { Object.values(sounds).forEach(audio => { audio.pause(); audio.currentTime = 0; }); }
  const musicPhases = new Set(["menu", "settings", "tutorial"]);
  let musicContext = null, musicBuffer = null, musicSource = null, musicGain = null;
  let musicLoading = false, musicFailed = false;
  function syncMenuMusic() {
    const wanted = connected && musicPhases.has(state.phase);
    if (!wanted) {
      if (musicSource) {
        const source = musicSource, gain = musicGain;
        musicSource = musicGain = null;
        const now = musicContext.currentTime;
        gain.gain.cancelScheduledValues(now);
        gain.gain.setValueAtTime(gain.gain.value, now);
        gain.gain.linearRampToValueAtTime(0, now + 0.08);
        source.onended = () => { source.disconnect(); gain.disconnect(); };
        source.stop(now + (musicContext.state === "running" ? 0.08 : 0));
      }
      return;
    }
    if (musicSource || musicLoading || musicFailed) return;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    if (!musicContext) {
      musicContext = new AudioContext();
      // The TV allows autoplay; ordinary browsers resume on the first input below.
      musicContext.resume().catch(() => {});
    }
    if (!musicBuffer) {
      musicLoading = true;
      fetch("audio/menu-loop_elevenlabs.io.wav")
        .then(response => { if (!response.ok) throw new Error("Menu music unavailable"); return response.arrayBuffer(); })
        .then(bytes => musicContext.decodeAudioData(bytes))
        .then(buffer => { musicBuffer = buffer; musicLoading = false; syncMenuMusic(); })
        .catch(error => { musicLoading = false; musicFailed = true; console.warn("Menu music could not load:", error.message); });
      return;
    }
    // A decoded PCM buffer loops on the audio clock, with no media-element restart gap.
    musicSource = musicContext.createBufferSource();
    musicSource.buffer = musicBuffer;
    musicSource.loop = true;
    musicGain = musicContext.createGain();
    musicGain.gain.setValueAtTime(0, musicContext.currentTime);
    musicGain.gain.linearRampToValueAtTime(0.45, musicContext.currentTime + 0.15);
    musicSource.connect(musicGain);
    musicGain.connect(musicContext.destination);
    musicSource.start();
  }
  function unlockMusic() {
    musicFailed = false;
    if (musicContext && musicContext.state === "suspended") musicContext.resume().catch(() => {});
    syncMenuMusic();
  }
  const logo = '<div class="logo-stage"><img class="logo" src="scavenger-hunt-q-logo.svg" alt="Scavenger Hunt Q" /></div>';
  const back = '<button class="secondary" data-command="back_to_menu">Menu</button>';
  app.innerHTML = `
    <div class="ribbons" aria-hidden="true"><i></i><i></i></div>
    <section id="welcome" class="screen welcome-screen" aria-label="Getting ready">
      ${logo}<div class="loading-copy"><h1>Getting the hunt ready…</h1><span class="activity" aria-hidden="true"></span><p id="startup-detail">Connecting to the game.</p></div>
    </section>
    <section id="menu" class="screen menu-screen" hidden aria-label="Main menu">
      ${logo}<p class="tagline">Search it, Show it, Score it</p>
      <nav class="menu-actions" aria-label="Game controls"><button class="primary play-button" data-command="start_game">Play</button><div class="action-row"><button class="secondary" data-command="show_tutorial">Tutorial</button><button class="secondary" data-command="open_settings">Settings</button></div></nav>
    </section>
    <section id="settings" class="screen standard-screen" hidden aria-labelledby="settings-title">
      <h1 id="settings-title">Round duration</h1><p>Choose how long you have to find each object.</p>
      <div class="timer-options" role="radiogroup" aria-label="Seconds per round">
        ${[30, 60, 120].map(seconds => `<button class="timer-choice secondary" role="radio" aria-checked="false" data-seconds="${seconds}"><strong>${seconds}</strong><span>seconds</span><small>Selected ✓</small></button>`).join("")}
      </div><div class="action-row">${back}</div>
    </section>
    <section id="tutorial" class="screen tutorial-screen" hidden aria-labelledby="tutorial-title">
      <p class="eyebrow">Two players. One riddle.</p><h1 id="tutorial-title">How to hunt</h1>
      <ol class="tutorial-steps">
        <li><span class="step-number">1</span><svg viewBox="0 0 160 110" aria-hidden="true"><rect x="42" y="8" width="78" height="94" rx="8"/><path d="M59 33h42M59 48h28M59 78h22"/><text x="100" y="85">?</text></svg><h2>Read the riddle</h2><p>Both players get the same clue.</p></li>
        <li><span class="step-number">2</span><svg viewBox="0 0 160 110" aria-hidden="true"><path d="M40 34h65v49q0 18-18 18H58q-18 0-18-18Z"/><path d="M106 45h12q24 0 18 23-4 14-30 12M55 10v10M77 5v15M98 10v10"/></svg><h2>Find the object</h2><p>Look around you for the answer.</p></li>
        <li><span class="step-number">3</span><svg viewBox="0 0 160 110" aria-hidden="true"><rect x="10" y="15" width="140" height="85" rx="8"/><path class="dashed" d="M80 18v78"/><circle cx="46" cy="45" r="12"/><path d="M27 89V72q19-24 38 0v17"/><rect x="107" y="45" width="23" height="31" rx="3"/></svg><h2>Show it on your side</h2><p>Hold it clearly inside your half.</p></li>
      </ol>
      <div class="side-demo"><div class="red"><strong>Red · Left</strong><span>Your half of the camera</span></div><div class="blue"><strong>Blue · Right</strong><span>Your half of the camera</span></div></div>
      <p class="tutorial-rule">The first correct object scores. Find it faster for more points.</p>
      <div class="action-row"><button class="primary" data-command="start_game">Play</button>${back}</div>
    </section>
    <section id="game" class="game-screen" hidden aria-label="Live hunt">
      <div class="camera-field"><img id="camera-feed" class="camera-feed" alt="Live camera: Red on the left, Blue on the right" />
        <svg id="recognition-overlay" class="recognition-overlay" viewBox="0 0 1440 1080" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          ${["red", "blue"].map(team => `<g id="recognition-${team}" class="recognition-box ${team}" style="display:none"><rect rx="26" vector-effect="non-scaling-stroke"/><text text-anchor="end"></text></g>`).join("")}
        </svg>
        <div class="camera-divider" aria-hidden="true"></div>
        <div class="hud"><div class="score-capsule red" aria-label="Red score"><strong id="red-score">0</strong></div><div class="time-capsule"><strong id="time-value"></strong><span id="round-label">Round 1 / 10</span></div><div class="score-capsule blue" aria-label="Blue score"><strong id="blue-score">0</strong></div></div>
        <div id="countdown-panel" class="countdown-panel" hidden><div id="countdown-number" class="countdown-number">3</div></div>
        <div id="clue" class="clue-box" hidden></div>
        <div id="preparing-panel" class="feedback-panel" hidden role="status"><span class="activity" aria-hidden="true"></span><h2>Preparing the next riddle…</h2><p>Your time hasn't started.</p></div>
        <div id="result-panel" class="feedback-panel result-panel" hidden role="status"><h2 id="result-title"></h2><p id="result-answer"></p><strong id="result-points"></strong><p id="result-next"></p></div>
        <div id="recovery-panel" class="feedback-panel recovery-panel" hidden role="status"><h2 id="recovery-title">Camera unavailable</h2><p id="recovery-detail">The hunt will continue automatically when the camera reconnects.</p><div class="action-row"><button id="recovery-action" class="primary" data-command="resume_game" hidden>Play</button><button class="secondary" data-command="back_to_menu">Menu</button></div></div>
      </div>
    </section>
    <section id="game-over" class="screen game-over-screen" hidden aria-labelledby="final-title">
      <h1 id="final-title">It's a draw!</h1><div class="final-scores"><div class="final-score red"><span>Red player</span><strong id="final-red">0</strong></div><div class="final-score blue"><span>Blue player</span><strong id="final-blue">0</strong></div></div>
      <h2 id="recap-title">10 rounds complete</h2><div id="recap" class="recap-tables" aria-label="All round results"></div>
      <div class="action-row"><button class="primary" data-command="start_game">Play again</button><button class="secondary" data-command="back_to_menu">Menu</button></div>
    </section>
    <div class="utility-controls"><button id="pause-control" class="utility" data-command="pause_game" hidden>Pause</button></div>
    <div id="connection-panel" class="connection-panel" hidden role="alert"><h2>Game temporarily unavailable</h2><p>Waiting for the game to respond.</p><span class="activity" aria-hidden="true"></span></div>
    <p id="announcement" class="sr-only" aria-live="polite" aria-atomic="true"></p>
  `;
  app.querySelectorAll("button").forEach(button => { button.tabIndex = -1; });
  const byId = id => document.getElementById(id);
  const camera = byId("camera-feed");
  camera.addEventListener("error", () => { cameraNeedsRefresh = true; });
  const text = (id, value) => { const node = byId(id); const next = String(value ?? ""); if (node.textContent !== next) node.textContent = next; };
  const visible = (id, show) => { byId(id).hidden = !show; };
  const integer = value => Math.max(0, Math.floor(Number(value) || 0));
  const player = value => value === "red" ? "Red" : value === "blue" ? "Blue" : "Time up";
  function renderRecognition() {
    const available = connected && cameraPhases.has(state.phase) && state.camera_ready !== false && state.inference_ready !== false && !cameraNeedsRefresh;
    if (!available || performance.now() - recognitionAt > recognitionLifetime) recognition = { red: null, blue: null };
    for (const team of ["red", "blue"]) {
      const group = byId(`recognition-${team}`);
      const detection = recognition[team];
      group.style.display = detection ? "" : "none";
      if (!detection) continue;
      const [x1, y1, x2, y2] = detection.box;
      const rectangle = group.querySelector("rect");
      for (const [name, value] of Object.entries({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 })) rectangle.setAttribute(name, value);
      const label = group.querySelector("text");
      label.setAttribute("x", x2 - 16);
      label.setAttribute("y", y2 - 20);
      label.textContent = detection.label;
    }
  }
  function acceptRecognition(next) {
    recognition = { red: null, blue: null };
    for (const team of ["red", "blue"]) {
      const detection = next && next[team];
      if (!detection || typeof detection.label !== "string" || !Array.isArray(detection.box) || detection.box.length !== 4 || !detection.box.every(Number.isFinite)) continue;
      recognition[team] = detection;
    }
    recognitionAt = performance.now();
    renderRecognition();
  }
  function secondsLeft(key) {
    const elapsed = connected && !state.paused ? (performance.now() - receivedAt) / 1000 : 0;
    return Math.max(0, Number(state[key] || 0) - elapsed);
  }
  function updateClock() {
    const seconds = Math.ceil(secondsLeft("round_remaining"));
    const urgent = connected && !state.paused && state.phase === "round" && seconds > 0 && seconds <= 5;
    byId("time-value").classList.toggle("timer-urgent", urgent);
    if (!connected) return;
    if (state.phase === "round") {
      text("time-value", `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`);
      const milestone = seconds > 0 && seconds < state.round_seconds && seconds % 10 === 0;
      if (!state.paused && (milestone || urgent) && (lastRoundSecond === null || seconds < lastRoundSecond) && !soundBaseline) {
        const progress = Math.max(0, Math.min(1, 1 - seconds / state.round_seconds));
        play("countdown-tick", 0.2 + 0.45 * progress);
      }
      lastRoundSecond = lastRoundSecond === null ? seconds : Math.min(lastRoundSecond, seconds);
    } else {
      text("time-value", state.phase === "countdown" ? "Get ready" : state.phase === "round_result" ? "Time's up" : state.recovery_reason === "paused" ? "Paused" : "On hold");
      // Recovery resumes the same round; do not replay its last tick.
      if (state.phase !== "recovery") lastRoundSecond = null;
    }
    if (state.phase === "countdown") {
      const number = Math.ceil(secondsLeft("countdown_remaining"));
      text("countdown-number", number > 0 ? number : "Ready");
      if (number > 0 && (lastCountdown === null || number < lastCountdown) && !soundBaseline) play("countdown-tick");
      lastCountdown = lastCountdown === null ? number : Math.min(lastCountdown, number);
    } else lastCountdown = null;
  }
  function renderRecap() {
    const signature = JSON.stringify(state.recaps);
    if (signature === recapSignature) return;
    recapSignature = signature;
    const container = byId("recap");
    container.replaceChildren();
    const rounds = Array.isArray(state.recaps) ? state.recaps : [];
    for (let offset = 0; offset < rounds.length; offset += 5) {
      const table = document.createElement("table");
      const caption = document.createElement("caption");
      caption.className = "sr-only";
      caption.textContent = `Rounds ${offset + 1} to ${Math.min(offset + 5, rounds.length)}`;
      table.append(caption);
      const head = document.createElement("thead");
      const header = document.createElement("tr");
      ["Round", "Object", "Winner", "Points"].forEach(label => { const th = document.createElement("th"); th.scope = "col"; th.textContent = label; header.append(th); });
      head.append(header); table.append(head);
      const body = document.createElement("tbody");
      rounds.slice(offset, offset + 5).forEach(round => {
        const tr = document.createElement("tr");
        [integer(round.round_number), round.target, player(round.winner), integer(round.points)].forEach((value, column) => {
          const td = document.createElement("td");
          td.textContent = String(value ?? "");
          if (column === 2) td.className = `winner ${round.winner === "red" ? "red" : round.winner === "blue" ? "blue" : "timeout"}`;
          tr.append(td);
        });
        body.append(tr);
      });
      table.append(body); container.append(table);
    }
  }
  function render() {
    const phase = state.phase;
    const inGame = cameraPhases.has(phase);
    app.dataset.phase = phase;
    app.classList.toggle("playing", inGame);
    ["welcome", "menu", "settings", "tutorial", "game", "game-over"].forEach(id => visible(id,
      id === "game" ? inGame : id === "welcome" ? ["boot", "loading"].includes(phase) : id === "game-over" ? phase === "game_over" : phase === id));
    if (inGame && !camera.getAttribute("src")) camera.src = "/camera";
    byId("game").classList.toggle("camera-unavailable", !connected || state.camera_ready === false);
    text("startup-detail", received ? "Getting the camera and clues ready." : "Connecting to the game.");
    text("red-score", integer(state.scores.red)); text("blue-score", integer(state.scores.blue));
    text("round-label", `Round ${Math.min(integer(state.current_round), integer(state.total_rounds))} / ${integer(state.total_rounds)}`);
    text("clue", state.clue);
    visible("clue", phase === "round"); visible("countdown-panel", phase === "countdown");
    visible("preparing-panel", phase === "preparing"); visible("result-panel", phase === "round_result"); visible("recovery-panel", phase === "recovery");
    visible("pause-control", connected && ["round", "countdown", "preparing", "round_result"].includes(phase));
    app.querySelectorAll("[data-seconds]").forEach(button => {
      const selected = Number(button.dataset.seconds) === Number(state.round_seconds);
      button.setAttribute("aria-checked", String(selected));
      button.classList.toggle("selected", selected);
    });
    if (phase === "preparing") {
      const heading = byId("preparing-panel").querySelector("h2");
      heading.textContent = state.camera_ready === false ? "Waiting for the camera…" : state.inference_ready === false ? "Getting object recognition ready…" : state.clue_error ? "Couldn't create a riddle" : "Preparing the next riddle…";
      byId("preparing-panel").querySelector("p").textContent = state.clue_error ? "Trying again. Your time hasn't started." : "Your time hasn't started.";
    }
    if (phase === "recovery") {
      const paused = state.paused && (!state.recovery_reason || /pause/i.test(state.recovery_reason));
      const connection = state.recovery_reason === "connection";
      const recognition = state.recovery_reason === "recognition";
      text("recovery-title", paused ? "Hunt paused" : connection ? "Connection interrupted" : recognition ? "Recognition unavailable" : "Camera unavailable");
      text("recovery-detail", paused ? "Take a breather. Your scores and time are saved." : connection ? "The hunt will continue automatically when the game reconnects." : recognition ? "The hunt is on hold. It will continue automatically." : "The hunt will continue automatically when the camera reconnects.");
      visible("recovery-action", paused);
    }
    if (phase === "round_result") {
      const result = state.result || state.recaps[state.recaps.length - 1] || {};
      const won = result.winner === "red" || result.winner === "blue";
      text("result-title", won ? `${player(result.winner)} found the ${result.target || state.target_name || "answer"}!` : "Time's up!");
      text("result-answer", won ? "" : `The answer was ${result.target || state.target_name || "the object"}.`);
      text("result-points", won ? `+${integer(result.points)} points` : "No points this round.");
      text("result-next", integer(state.current_round) >= integer(state.total_rounds) ? "Final scores coming up…" : "Next round coming up…");
      byId("result-panel").className = `feedback-panel result-panel ${won ? result.winner : "timeout"}`;
    }
    if (phase === "game_over") {
      text("final-title", state.scores.red > state.scores.blue ? "Red wins!" : state.scores.blue > state.scores.red ? "Blue wins!" : "It's a draw!");
      text("final-red", integer(state.scores.red)); text("final-blue", integer(state.scores.blue));
      text("recap-title", `${state.recaps.length} rounds complete`); renderRecap();
    }
    updateClock();
    renderRecognition();
    syncMenuMusic();
  }
  function setConnection(value) {
    if (connected === value) return;
    connected = value;
    if (!value) { soundBaseline = true; cameraNeedsRefresh = Boolean(camera.getAttribute("src")); stopSounds(); }
    app.classList.toggle("disconnected", !value);
    visible("connection-panel", !value && received);
    app.querySelectorAll("[data-command], [data-seconds]").forEach(button => { button.disabled = !value; });
    render();
  }
  function acceptState(next) {
    if (!next || typeof next !== "object" || !next.phase) return;
    const before = state;
    // Revisions increase within a server process; a reconnect may legitimately restart them.
    if (connected && Number.isFinite(next.revision) && Number.isFinite(state.revision) && next.revision < state.revision) return;
    const baseline = soundBaseline || !received || !connected;
    state = { ...state, ...next, scores: { ...state.scores, ...next.scores } };
    if (before.phase !== state.phase && state.phase === "loading") stopSounds();
    receivedAt = performance.now(); received = true;
    setConnection(true);
    if (baseline) {
      lastCountdown = state.phase === "countdown" ? Math.ceil(Number(state.countdown_remaining) || 0) : null;
      lastRoundSecond = ["round", "recovery"].includes(state.phase) ? Math.ceil(Number(state.round_remaining) || 0) : null;
      lastResultRound = state.recaps.at(-1)?.round_number ?? (state.phase === "round_result" ? state.current_round : null);
    }
    if (["menu", "loading"].includes(state.phase)) lastResultRound = null;
    if (!baseline && before.phase !== state.phase) {
      if (state.phase === "round") play("hunt-start");
      if (state.phase === "round_result" && state.current_round !== lastResultRound) {
        const result = state.result || state.recaps[state.recaps.length - 1] || {};
        play(result.winner === "red" || result.winner === "blue" ? "object-found" : "time-up");
      }
      if (state.phase === "game_over") play("match-complete");
    }
    if (state.phase === "round_result") lastResultRound = state.current_round;
    render(); soundBaseline = false;
    if (before.phase !== state.phase) {
      text("announcement", state.phase === "round" ? state.clue : state.phase === "round_result" ? byId("result-title").textContent : state.phase === "game_over" ? byId("final-title").textContent : "");
    }
  }
  // The Arduino bridge is intentionally isolated from the renderer.
  const ui = typeof WebUI !== "undefined" ? new WebUI() : null;
  function send(command, payload = {}) {
    if (!connected || !ui) return;
    play("ui-confirm");
    ui.send_message(command, payload);
  }
  app.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.seconds) { send("set_round_seconds", { seconds: Number(button.dataset.seconds) }); return; }
    if (button.dataset.command) send(button.dataset.command);
  });
  // This kiosk accepts pointer/voice controls and the remote CLI, never keyboard input.
  ["keydown", "keypress", "keyup"].forEach(type => document.addEventListener(type, event => {
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true));
  document.addEventListener("pointerdown", unlockMusic);
  if (ui) {
    ui.on_message("game_state", acceptState);
    ui.on_message("recognition", acceptRecognition);
    ui.on_disconnect(() => setConnection(false));
    ui.on_connect(() => { soundBaseline = true; });
  }
  render();
  app.querySelectorAll("[data-command], [data-seconds]").forEach(button => { button.disabled = true; });
  setInterval(() => {
    if (received && performance.now() - receivedAt > 5000) setConnection(false);
    updateClock();
    renderRecognition();
    // Restart only a failed/interrupted request, never the element or an ordinary HUD tick.
    if (cameraNeedsRefresh && connected && state.camera_ready && cameraPhases.has(state.phase) && performance.now() >= cameraRetryAt) {
      cameraNeedsRefresh = false;
      cameraRetryAt = performance.now() + 2000;
      camera.src = `/camera?retry=${Date.now()}`;
    }
  }, 100);
  setTimeout(() => {
    if (!received) text("startup-detail", "The game is taking a little longer. Check that the board is running and connected.");
  }, 12000);
})();
