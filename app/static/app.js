/* Flop7 frontend logic — no build step, no framework (spec 6). */
(function () {
  "use strict";

  const I18n = window.Flop7I18n;
  const Storage = window.Flop7Storage;
  const Theme = window.Flop7Theme;
  const t = I18n.t;

  // Stable English API messages (spec 2.2 + API) mapped to dictionary keys.
  const ERROR_KEYS = {
    "game is over": "error.gameOver",
    "start the next round first": "error.startNextRound",
    "round is still running": "error.roundRunning",
    "not your turn": "error.notYourTurn",
    "choose a target first": "error.chooseTarget",
    "nothing to target": "error.nothingToTarget",
    "not your decision": "error.notYourDecision",
    "invalid target": "error.invalidTarget",
    "no cards left": "error.noCardsLeft",
    "too many automatic moves": "error.tooManyAutomatic",
    "a game needs 2 to 6 players including the optional bot": "error.badPlayerCount",
    "this device does not control that seat": "error.tokenRequired",
    "game not found": "error.gameNotFound",
    "game changed in the meantime, reload and try again": "error.staleWrite",
    "each name must be 1 to 20 characters after trimming": "error.badNames",
    "invalid request": "error.invalidRequest"
  };

  function translateError(detail) {
    if (!detail) return t("error.network");
    const key = ERROR_KEYS[String(detail)];
    return key ? t(key) : String(detail);
  }

  // ---- state ----
  let game = null;
  let gameId = null;
  let mySeat = 0;
  let tokens = {};
  let hotseat = true;
  let inviteSeats = [];
  let stream = null;
  let reconnectTimer = null;
  let toastTimer = null;
  let inviteFeedbackTimer = null;
  let lastEventId = 0;
  let busy = false;
  let soundOn = Storage.get("sound") !== false;
  let audioCtx = null;
  // Opening deals arrive as one view; remember which cards are new so they can be
  // revealed one by one instead of appearing all at once (see renderPlayers).
  let lastSeatCards = null;
  let cardDealAt = {};
  let newCardUntil = {};
  const DEAL_STEP_MS = 180;
  const DEAL_DURATION_MS = 380;
  // A sleeping free-tier host can take ~1 min to answer the first request.
  const WAKE_DELAY_MS = 5000;
  const SAFE_RETRY_DELAYS_MS = [1500, 3000];
  let pendingRequests = 0;
  let retryFn = null;
  // UI state that must survive re-renders without rebuilding the dock or dialogs.
  let loading = false;
  let creating = false;
  let dockSignature = "";
  let inviteSignature = "";
  let eventTimer = null;
  let lastTurnSeat = null;

  const $ = function (id) { return document.getElementById(id); };
  const els = {
    setupScreen: $("setup-screen"), tableScreen: $("table-screen"),
    form: $("setup-form"), names: $("names"), botChip: $("bot-chip"),
    addBot: $("add-bot"), hotseat: $("hotseat"), preview: $("participant-preview"),
    setupError: $("setup-error"),
    players: $("players"), dealHint: $("deal-hint"), winnerBanner: $("winner-banner"),
    winnerTitle: $("winner-title"), winnerMargin: $("winner-margin"),
    summary: $("round-summary"), summaryTitle: $("round-summary-title"),
    results: $("round-results"),
    dockRound: $("dock-round"), dockDeck: $("dock-deck"),
    dockPrompt: $("dock-prompt"), dockActions: $("dock-actions"),
    waitingFill: $("waiting-fill"),
    toast: $("toast"), toastText: $("toast-text"), toastRetry: $("toast-retry"),
    wake: $("wake-banner"), chip: $("connection-chip"), storageHint: $("storage-hint"),
    loadingScreen: $("loading-screen"),
    dockEvent: $("dock-event"), tieBanner: $("tie-banner"),
    btnRules: $("btn-rules"), btnLanguage: $("btn-language"),
    btnSound: $("btn-sound"), btnTheme: $("btn-theme"), btnNewGame: $("btn-new-game"),
    inviteDialog: $("invite-dialog"), inviteList: $("invite-list"),
    inviteFeedback: $("invite-feedback"),
    rulesDialog: $("rules-dialog"), cardDialog: $("card-dialog"),
    cardExplanation: $("card-explanation")
  };

  // ---- small helpers ----
  function maxId(events) {
    const ids = (events || []).map(function (e) { return e && e.id; })
      .filter(function (n) { return typeof n === "number"; });
    return ids.length ? Math.max.apply(null, ids) : 0;
  }

  function toast(message, retry) {
    els.toastText.textContent = message;
    retryFn = typeof retry === "function" ? retry : null;
    els.toastRetry.hidden = !retryFn;
    els.toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.hidden = true; }, retryFn ? 8000 : 3500);
  }

  function setSetupError(message) {
    els.setupError.textContent = message;
    els.setupError.hidden = false;
  }

  function clearSetupError() {
    els.setupError.textContent = "";
    els.setupError.hidden = true;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function makeButton(className, label, action, value) {
    const button = el("button", "btn " + className, label);
    button.type = "button";
    button.setAttribute("data-action", action);
    if (value !== undefined) button.setAttribute("data-value", String(value));
    return button;
  }

  // Action cards travel through the API as internal keys; never show those to players.
  function cardName(value) {
    const text = String(value);
    return text === "freeze" || text === "flip3" || text === "second"
      ? t("cardName." + text) : text;
  }

  function playerName(seat) {
    return game && game.players[seat] ? game.players[seat].name : String(seat);
  }

  function eventText(event) {
    const seat = event.seat !== undefined ? event.seat : event.by;
    const name = playerName(seat);
    const target = event.target !== undefined ? playerName(event.target) : "";
    const card = event.card !== undefined ? cardName(event.card) : "";
    switch (event.type) {
      case "draws": return t("event.draws", { name: name, card: card });
      case "bust": return t("event.bust", { name: name, card: card });
      case "second": return t(event.used ? "event.secondUsed" : "event.secondGained", { name: name });
      case "gives": return t("event.gives", { name: name, target: target });
      case "freezes": return t("event.freezes", { name: name, target: target });
      case "flip3": return t("event.flip3", { name: name, target: target });
      case "flop7": return t("event.flop7", { name: name });
      case "reshuffle": return t("event.reshuffle");
      case "next": return t("event.next", { round: event.round });
      default: return "";
    }
  }

  function showEvent(event) {
    const text = eventText(event);
    if (!text) return;
    els.dockEvent.textContent = text;
    if (eventTimer) clearTimeout(eventTimer);
    eventTimer = setTimeout(function () { els.dockEvent.textContent = ""; }, 4000);
  }

  function clearEvent() {
    if (eventTimer) clearTimeout(eventTimer);
    eventTimer = null;
    els.dockEvent.textContent = "";
  }

  // ---- network ----
  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function showWake() {
    if (pendingRequests > 0) els.wake.hidden = false;
  }

  async function api(path, options) {
    options = options || {};
    const method = (options.method || "GET").toUpperCase();
    const safe = method === "GET" || method === "HEAD";
    const headers = Object.assign({}, options.headers || {});
    if (options.body) headers["Content-Type"] = "application/json";
    const delays = safe ? SAFE_RETRY_DELAYS_MS : [];
    const requestOptions = Object.assign({}, options, { headers: headers });
    delete requestOptions.retry;
    pendingRequests += 1;
    const wakeTimer = setTimeout(showWake, WAKE_DELAY_MS);
    try {
      let error = { status: 0, detail: t("error.network"), retry: options.retry };
      for (let attempt = 0; attempt <= delays.length; attempt += 1) {
        if (attempt > 0) await sleep(delays[attempt - 1]);
        let response;
        try {
          response = await fetch(path, requestOptions);
        } catch (_networkError) {
          continue;
        }
        let data = null;
        try { data = await response.json(); } catch (_parseError) { data = null; }
        if (response.ok) return data;
        error = {
          status: response.status,
          detail: (data && data.detail) || ("HTTP " + response.status),
          retry: options.retry
        };
        if (response.status < 500) break;
      }
      throw error;
    } finally {
      clearTimeout(wakeTimer);
      pendingRequests = Math.max(0, pendingRequests - 1);
      if (pendingRequests === 0) els.wake.hidden = true;
    }
  }

  els.toastRetry.addEventListener("click", function () {
    const fn = retryFn;
    retryFn = null;
    els.toastRetry.hidden = true;
    els.toast.hidden = true;
    if (!fn) return;
    Promise.resolve().then(fn).catch(function (err) {
      toast(translateError(err.detail), err.status === 0 || err.status >= 500 ? fn : null);
    });
  });

  async function reloadGame() {
    if (!gameId) return;
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId), {
        retry: function () { return reloadGame(); }
      });
      applyView(view);
    } catch (err) {
      toast(translateError(err.detail), err.retry);
    }
  }

  // ---- storage (defensive, namespaced) ----
  function saveGameData() {
    if (!gameId) return;
    Storage.set("game:" + gameId, {
      tokens: tokens, mySeat: mySeat, hotseat: hotseat, invites: inviteSeats
    });
  }

  function loadGameData(id) {
    const data = Storage.get("game:" + id);
    if (!data || typeof data !== "object" || !data.tokens || typeof data.tokens !== "object") return null;
    const clean = {};
    Object.keys(data.tokens).forEach(function (key) {
      const seat = Number(key);
      if (Number.isInteger(seat) && seat >= 0 && seat <= 5 && typeof data.tokens[key] === "string") {
        clean[seat] = data.tokens[key];
      }
    });
    const keys = Object.keys(clean);
    if (!keys.length) return null;
    const savedSeat = Number(data.mySeat);
    return {
      tokens: clean,
      mySeat: Number.isInteger(savedSeat) && clean[savedSeat] ? savedSeat : Number(keys[0]),
      hotseat: data.hotseat === true,
      invites: Array.isArray(data.invites)
        ? data.invites.filter(function (x) {
            return x && Number.isInteger(x.seat) && typeof x.token === "string" && typeof x.name === "string";
          })
        : []
    };
  }

  // ---- seats / permissions ----
  function controls(seat) {
    return Object.prototype.hasOwnProperty.call(tokens, seat) && (hotseat || seat === mySeat);
  }

  function anyControl() {
    return Object.keys(tokens).some(function (key) { return controls(Number(key)); });
  }

  function actorSeat() {
    if (!game) return mySeat;
    if (game.pending && game.pending.length && Number.isInteger(game.pending[0].by)) {
      return game.pending[0].by;
    }
    if (!game.round_over && game.winner === null) return game.turn;
    return mySeat; // next / rematch: any token holder
  }

  function isBotTurn() {
    return !!game && !game.round_over && game.winner === null &&
      !!game.players[game.turn] && game.players[game.turn].bot === true;
  }

  function canHitOrStay() {
    return !!game && !busy && !game.round_over && game.winner === null &&
      !(game.pending && game.pending.length) && !isBotTurn() && controls(game.turn);
  }

  function canTarget() {
    return !!game && !busy && game.pending && game.pending.length &&
      controls(game.pending[0].by) && game.targets && game.targets.length > 0;
  }

  // ---- view / events ----
  function applyView(view) {
    const first = game === null;
    if (first || view.id !== gameId) {
      lastSeatCards = null;
      cardDealAt = {};
      newCardUntil = {};
      lastTurnSeat = null;
      dockSignature = "";
      inviteSignature = "";
      clearEvent();
    }
    game = view;
    gameId = view.id;
    if (first && view.events && view.events.length) lastEventId = maxId(view.events);
    else playNewEvents(view.events || []);
    render();
  }

  function playNewEvents(events) {
    events.forEach(function (event) {
      if (typeof event.id === "number" && event.id > lastEventId) {
        lastEventId = event.id;
        if (event.type === "next") lastSeatCards = null; // a fresh round starts empty
        showEvent(event);
        if (soundOn) beep(event.type);
      }
    });
  }

  function beep(kind) {
    if (!soundOn) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      if (!audioCtx) audioCtx = new Ctx();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const now = audioCtx.currentTime;
      function tone(freq, delay, duration) {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "square";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, now + delay);
        gain.gain.exponentialRampToValueAtTime(0.08, now + delay + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + delay + duration);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(now + delay);
        osc.stop(now + delay + duration + 0.02);
      }
      if (kind === "draws") tone(660, 0, 0.07);
      else if (kind === "bust") { tone(220, 0, 0.11); tone(160, 0.12, 0.15); }
      else if (kind === "round" || kind === "win" || kind === "flop7") {
        tone(523, 0, 0.11); tone(659, 0.12, 0.11); tone(784, 0.24, 0.17);
      } else tone(440, 0, 0.06);
    } catch (_error) { /* sound is optional; never break the game */ }
  }

  // ---- rendering ----
  function inviteSignatureKey() {
    return JSON.stringify(inviteSeats.map(function (s) { return [s.seat, s.name, s.token]; }));
  }

  function render() {
    I18n.apply(document);
    els.storageHint.hidden = Storage.available();
    const inGame = game !== null;
    els.loadingScreen.hidden = !loading;
    document.body.classList.toggle("is-loading", loading);
    document.body.classList.toggle("is-setup", !inGame && !loading);
    els.btnNewGame.hidden = !inGame;
    els.setupScreen.hidden = inGame || loading;
    els.tableScreen.hidden = !inGame || loading;
    renderTopbar();
    updatePreview();
    if (inGame && !loading) {
      renderPlayers();
      renderSummary();
      renderWinner();
      renderTie();
      renderDealHint();
    } else {
      els.players.textContent = "";
      els.dealHint.classList.add("is-placeholder");
      els.summary.hidden = true;
      els.winnerBanner.hidden = true;
      els.tieBanner.hidden = true;
      lastSeatCards = null;
      cardDealAt = {};
      newCardUntil = {};
    }
    renderDock();
    updateTitle();
    const sig = inviteSignatureKey();
    if (els.inviteDialog.open && sig !== inviteSignature) {
      renderInviteList();
      fitInviteLinks();
      inviteSignature = sig;
    }
  }

  function renderTopbar() {
    const mode = Theme.get();
    const suffix = mode === "auto" ? "Auto" : mode === "light" ? "Light" : "Dark";
    const themeLabel = t("nav.theme" + suffix);
    els.btnTheme.textContent = mode === "auto" ? "◐" : mode === "light" ? "☀" : "☾";
    els.btnTheme.setAttribute("aria-label", themeLabel);
    els.btnTheme.setAttribute("title", themeLabel);
    const soundLabel = t(soundOn ? "nav.soundOn" : "nav.soundOff");
    els.btnSound.textContent = soundOn ? "🔊" : "🔇";
    els.btnSound.setAttribute("aria-pressed", soundOn ? "true" : "false");
    els.btnSound.setAttribute("aria-label", soundLabel);
    els.btnSound.setAttribute("title", soundLabel);
  }

  function updatePreview() {
    const names = els.names.value.split(",").map(function (n) { return n.trim(); }).filter(Boolean);
    if (els.addBot.checked) names.push(t("setup.botChip"));
    els.botChip.hidden = !els.addBot.checked;
    const joined = names.join(" · ");
    els.preview.textContent = names.length === 1
      ? t("setup.previewOne", { names: joined })
      : t("setup.previewMany", { names: joined, count: names.length });
  }

  function statusFor(player, seat) {
    if (isBotTurn() && seat === game.turn) return { key: "status.thinking", cls: "status-thinking" };
    if (player.status === "bust") return { key: "status.bust", cls: "status-bust" };
    if (player.status === "stayed") return { key: "status.stayed", cls: "status-stayed" };
    if (game.round_over) return { key: "status.finished", cls: "status-finished" };
    return null; // active is implicit
  }

  function newestCardFor(seat) {
    const events = game.events || [];
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (events[i].type === "draws" && events[i].seat === seat) return events[i].card;
    }
    return null;
  }

  function dealKey(seat, index, value) {
    return gameId + ":" + game.round + ":" + seat + ":" + index + ":" + value;
  }

  function renderPlayers() {
    els.players.textContent = "";
    const current = game.players.map(function (player) {
      return player.numbers.concat(player.mods).map(String);
    });
    const fresh = current.map(function (cards, seat) {
      const previous = (lastSeatCards && lastSeatCards[seat]) || [];
      return cards.map(function (card, index) {
        return index >= previous.length || previous[index] !== card;
      });
    });

    // The engine deals in turn order. Reveal freshly dealt cards one by one in
    // that order so the opening deal is visible. A re-render reuses the absolute
    // start times; a negative CSS animation-delay resumes instead of restarting.
    const now = Date.now();
    const firstRender = lastSeatCards === null; // cards already on the table when we arrived
    const count = game.players.length;
    const freshList = [];
    fresh.forEach(function (flags, seat) {
      flags.forEach(function (isFresh, index) {
        if (isFresh) freshList.push({ seat: seat, index: index, order: (seat - game.turn + count) % count });
      });
    });
    freshList.sort(function (a, b) { return a.order - b.order || a.index - b.index; });
    freshList.forEach(function (entry, step) {
      const key = dealKey(entry.seat, entry.index, current[entry.seat][entry.index]);
      if (cardDealAt[key] === undefined) cardDealAt[key] = now + step * DEAL_STEP_MS;
      if (!firstRender && newCardUntil[key] === undefined) newCardUntil[key] = now + 2500;
    });
    Object.keys(cardDealAt).forEach(function (key) {
      if (cardDealAt[key] < now - (DEAL_DURATION_MS + 2000)) delete cardDealAt[key];
    });
    Object.keys(newCardUntil).forEach(function (key) {
      if (newCardUntil[key] < now) delete newCardUntil[key];
    });

    game.players.forEach(function (player, seat) {
      const card = el("div", "player-card");
      if (!game.round_over && game.winner === null && seat === game.turn) card.classList.add("is-turn");
      if (!hotseat && seat === mySeat) card.classList.add("is-mine");
      if (game.pending && game.pending.length && game.targets.indexOf(seat) !== -1) {
        card.classList.add("is-target");
      }

      const name = el("h3", "player-name");
      if (player.bot) name.appendChild(el("span", null, "🤖"));
      if (!game.round_over && game.winner === null && seat === game.turn) {
        name.appendChild(el("span", "turn-marker", "▶"));
      }
      name.appendChild(el("span", null, player.name));
      if (!hotseat && seat === mySeat) name.appendChild(el("span", "you-tag", t("table.you")));
      card.appendChild(name);

      card.appendChild(el("p", "player-line", t("table.total", { total: player.total })));
      card.appendChild(el("p", "player-line", t("table.score", { score: player.score })));
      card.appendChild(el("p", "player-line", t("table.numbers", { count: player.numbers.length })));

      const progress = el("div", "progress");
      for (let i = 0; i < 7; i += 1) {
        progress.appendChild(el("span", "progress-step" + (i < player.numbers.length ? " filled" : "")));
      }
      card.appendChild(progress);

      // Reserve the badge row so gaining a status never changes the card height.
      const statusSlot = el("div", "player-status");
      if (game.pending && game.pending.length && game.targets.indexOf(seat) !== -1) {
        statusSlot.appendChild(el("span", "target-flag", t("table.target")));
      }
      if (player.second) statusSlot.appendChild(el("span", "second-flag", t("table.second")));
      const status = statusFor(player, seat);
      if (status) statusSlot.appendChild(el("span", "status-badge " + status.cls, t(status.key)));
      if (player.frozen === true) {
        statusSlot.appendChild(el("span", "status-badge status-frozen", t("status.frozen")));
      }
      card.appendChild(statusSlot);

      const newest = newestCardFor(seat);
      const cards = el("div", "cards");
      player.numbers.concat(player.mods).forEach(function (value, index) {
        const text = String(value);
        const kind = typeof value === "number" ? "number" : text.startsWith("+") ? "plus" : text === "x2" ? "x2" : "action";
        const button = el("button", "card card-" + kind + (value === newest ? " card-new" : ""), text);
        const dealAt = cardDealAt[dealKey(seat, index, text)];
        if (dealAt !== undefined) {
          button.classList.add("card-deal");
          button.style.animationDelay = (dealAt - Date.now()) + "ms";
        }
        button.type = "button";
        // Cards are informational; keeping them out of the tab order avoids
        // dozens of tab stops before the dock controls.
        button.tabIndex = -1;
        button.setAttribute("data-card", text);
        const announced = newCardUntil[dealKey(seat, index, text)] ? "newCard" : "card.label";
        button.setAttribute("aria-label", t(announced, { card: text }));
        cards.appendChild(button);
      });
      card.appendChild(cards);
      els.players.appendChild(card);
    });
    lastSeatCards = current;

    // Follow the active seat in hot-seat games: with several players the card
    // can be far below the fold, while the dock only shows the action.
    const turnChanged = lastTurnSeat !== null && lastTurnSeat !== game.turn;
    lastTurnSeat = game.turn;
    if (turnChanged && !document.querySelector("dialog[open]") &&
        !game.round_over && game.winner === null) {
      const active = els.players.querySelector(".player-card.is-turn");
      if (active) {
        const box = active.getBoundingClientRect();
        const dock = document.getElementById("dock");
        const limit = dock ? dock.getBoundingClientRect().top : window.innerHeight;
        if (box.top < 0 || box.bottom > limit) {
          const reduce = window.matchMedia &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          active.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
        }
      }
    }
  }

  // Explains the face-up card everyone already holds when a round begins.
  function renderDealHint() {
    const opening = !game.round_over && game.winner === null &&
      game.players.every(function (player) {
        return player.numbers.length + player.mods.length <= 1;
      });
    els.dealHint.classList.toggle("is-placeholder", !opening);
  }

  function renderSummary() {
    if (!game || !game.round_over || !game.last_round) {
      els.summary.hidden = true;
      return;
    }
    els.summary.hidden = false;
    els.summaryTitle.textContent = t("round.title", { round: game.round });
    els.results.textContent = "";
    game.last_round.forEach(function (result) {
      const name = game.players[result.seat] ? game.players[result.seat].name : String(result.seat);
      let text = t("round.result", { name: name, points: result.points });
      if (result.flop7) text += " " + t("round.flop7");
      els.results.appendChild(el("li", null, text));
    });
  }

  function renderWinner() {
    if (!game || game.winner === null) {
      els.winnerBanner.hidden = true;
      return;
    }
    const winner = game.players[game.winner];
    const totals = game.players.map(function (p) { return p.total; });
    const others = totals.filter(function (_value, index) { return index !== game.winner; });
    const margin = Math.max.apply(null, others);
    const gap = totals[game.winner] - margin;
    const scale = gap <= 15 ? "scale-1" : gap <= 30 ? "scale-2" : "scale-3";
    els.winnerBanner.className = "winner-banner " + scale;
    els.winnerBanner.hidden = false;
    els.winnerTitle.textContent = t("winner.title", { name: winner.name });
    els.winnerMargin.textContent = t("winner.margin", { margin: Math.max(0, gap) });
  }

  function renderTie() {
    if (!game || !game.round_over || game.winner !== null) {
      els.tieBanner.hidden = true;
      return;
    }
    const totals = game.players.map(function (p) { return p.total; });
    const best = Math.max.apply(null, totals);
    const leaders = totals.filter(function (value) { return value === best; });
    const show = best >= 200 && leaders.length > 1;
    els.tieBanner.hidden = !show;
    if (show) els.tieBanner.textContent = t("tie.title");
  }

  function setWaiting(level) {
    els.waitingFill.className = "waiting-fill w-" + level;
  }

  function renderDock() {
    if (!game || loading) {
      els.dockActions.textContent = "";
      dockSignature = "";
      if (!game) {
        // The bottom bar is the setup call to action; no prompt text needed.
        els.dockRound.textContent = "";
        els.dockPrompt.textContent = "";
        els.dockDeck.textContent = "";
        const start = makeButton("btn btn-primary", t("setup.start"), "start");
        start.id = "btn-start"; // stable hook for keyboard/mobile checks
        els.dockActions.appendChild(start);
      } else {
        els.dockPrompt.textContent = t("loading.title");
      }
      setWaiting("none");
      return;
    }
    els.dockRound.removeAttribute("data-i18n");
    els.dockPrompt.removeAttribute("data-i18n");
    els.dockRound.textContent = t("dock.round", { round: game.round });
    els.dockDeck.textContent = t("dock.deck", { count: game.deck_count });

    let promptText = "";
    let level = "none";
    const actions = [];

    if (game.winner !== null) {
      promptText = t("winner.title", { name: game.players[game.winner].name });
      if (anyControl()) actions.push(["btn-next", t("action.rematch"), "rematch", undefined]);
      if (inviteSeats.length) actions.push(["btn", t("action.invite"), "invite", undefined]);
      level = "ready";
    } else if (game.round_over) {
      promptText = t("dock.break");
      if (anyControl()) actions.push(["btn-next", t("action.next"), "next", undefined]);
      level = "ready";
    } else if (game.pending && game.pending.length) {
      const pending = game.pending[0];
      const kind = pending.kind === "give" ? "second" : pending.kind;
      promptText = t("dock.target", { card: cardName(kind) });
      if (controls(pending.by)) {
        game.targets.forEach(function (seat, index) {
          actions.push([
            "btn-target",
            t("dock.targetChoice", { n: index + 1, name: game.players[seat].name }),
            "target",
            seat
          ]);
        });
        level = "ready";
      } else {
        level = "wait";
      }
    } else if (isBotTurn()) {
      promptText = t("dock.thinking", { name: game.players[game.turn].name });
      level = "think";
    } else if (controls(game.turn)) {
      promptText = hotseat
        ? t("dock.yourTurnName", { name: game.players[game.turn].name })
        : t("dock.yourTurn");
      actions.push(["btn-hit", t("action.hit"), "hit", undefined]);
      actions.push(["btn-stay", t("action.stay"), "stay", undefined]);
      level = "ready";
    } else if (anyControl()) {
      promptText = t("dock.waiting", { name: game.players[game.turn].name });
      level = "wait";
    } else {
      promptText = t("dock.spectator");
      level = "none";
    }

    els.dockPrompt.textContent = promptText;
    setWaiting(level);
    const signature = actions.map(function (a) {
      return a[2] + ":" + (a[3] === undefined ? "" : a[3]) + ":" + a[1];
    }).join("|");
    if (signature === dockSignature) return;
    dockSignature = signature;
    els.dockActions.textContent = "";
    actions.forEach(function (a) {
      els.dockActions.appendChild(makeButton(a[0], a[1], a[2], a[3]));
    });
  }

  function updateTitle() {
    const base = "Flop7";
    let signal = "";
    if (game && game.winner === null && !game.round_over) {
      const actor = actorSeat();
      if (controls(actor) && !isBotTurn()) signal = "▶ ";
    }
    document.title = signal + base;
  }

  // ---- actions ----
  async function doMove(action, target) {
    if (busy || !game) return;
    busy = true;
    const seat = action === "next" ? mySeat : actorSeat();
    const body = { seat: seat, version: game.version };
    if (action === "target") body.target = target;
    const options = {
      method: "POST",
      headers: { "X-Seat-Token": tokens[seat] || "" },
      body: JSON.stringify(body),
      retry: function () { return doMove(action, target); }
    };
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId) + "/" + action, options);
      applyView(view);
    } catch (err) {
      toast(translateError(err.detail), err.retry);
      if (err.status === 409) await reloadGame();
    } finally {
      busy = false;
      render();
    }
  }

  async function startNewGame(names, withBot, after) {
    const view = await api("/api/games", {
      method: "POST",
      body: JSON.stringify({ players: names, bot: withBot }),
      retry: function () { return startNewGame(names, withBot, after); }
    });
    closeStream();
    tokens = {};
    view.seats.forEach(function (s) { tokens[s.seat] = s.token; });
    inviteSeats = view.seats
      .filter(function (s) { return !view.players[s.seat].bot; })
      .map(function (s) { return { seat: s.seat, name: s.name, token: s.token }; });
    gameId = view.id;
    saveGameData();
    history.replaceState(null, "", location.pathname + "?game=" + encodeURIComponent(gameId));
    lastEventId = 0;
    applyView(view);
    connectStream();
    if (typeof after === "function") after(view);
    return view;
  }

  async function createGame(event) {
    event.preventDefault();
    if (creating || loading) return; // a slow cold start must not create two games
    const names = els.names.value.split(",").map(function (n) { return n.trim(); }).filter(Boolean);
    const withBot = els.addBot.checked;
    if (names.length + Number(withBot) < 2) { setSetupError(t("setup.errorEmpty")); return; }
    if (names.length + Number(withBot) > 6) { setSetupError(t("setup.errorTooMany")); return; }
    if (names.some(function (n) { return n.length < 1 || n.length > 20; })) {
      setSetupError(t("setup.errorName"));
      return;
    }
    clearSetupError();
    creating = true;
    setStartBusy(true);
    mySeat = 0;
    hotseat = els.hotseat.checked;
    try {
      await startNewGame(names, withBot, function () {
        Storage.set("setup", { names: names.join(", "), bot: withBot, hotseat: hotseat });
        openInvitesAfterDeal();
      });
    } catch (err) {
      if (err.status === 0 || err.status >= 500) toast(translateError(err.detail), err.retry);
      else setSetupError(translateError(err.detail));
    } finally {
      creating = false;
      setStartBusy(false);
    }
  }

  function setStartBusy(busyNow) {
    const start = $("btn-start");
    if (!start) return;
    start.disabled = busyNow;
    start.textContent = t(busyNow ? "setup.starting" : "setup.start");
  }

  function leaveGame() {
    if (!game || loading || !window.confirm(t("leave.confirm"))) return;
    const id = gameId;
    closeStream();
    if (id) Storage.remove("game:" + id);
    game = null;
    gameId = null;
    tokens = {};
    inviteSeats = [];
    mySeat = 0;
    hotseat = true;
    busy = false;
    lastEventId = 0;
    lastSeatCards = null;
    cardDealAt = {};
    newCardUntil = {};
    lastTurnSeat = null;
    dockSignature = "";
    inviteSignature = "";
    clearEvent();
    history.replaceState(null, "", location.pathname);
    render();
  }

  async function rematch() {
    if (!game || busy) return;
    busy = true;
    const names = game.players.filter(function (p) { return !p.bot; })
      .map(function (p) { return p.name; });
    const withBot = game.players.some(function (p) { return p.bot; });
    try {
      await startNewGame(names, withBot, openInvitesAfterDeal); // mySeat + hotseat stay the same
    } catch (err) {
      toast(translateError(err.detail), err.retry);
    } finally {
      busy = false;
    }
  }

  // ---- invite links ----
  function inviteUrl(seat, token) {
    return location.origin + location.pathname + "?game=" + encodeURIComponent(gameId) +
      "#seat=" + seat + "&token=" + encodeURIComponent(token);
  }

  function renderInviteList() {
    els.inviteList.textContent = "";
    if (!game) return;
    inviteSeats.forEach(function (entry) {
      const row = el("div", "invite-row");
      const head = el("div", "invite-row-head");
      head.appendChild(el("span", null, t("invite.seat", { n: entry.seat + 1, name: entry.name })));
      if (entry.seat === mySeat) head.appendChild(el("span", "you-tag", t("invite.self")));
      row.appendChild(head);

      const link = inviteUrl(entry.seat, entry.token);
      const input = el("textarea", "invite-link");
      input.readOnly = true;
      input.rows = 2;
      input.value = link;
      input.setAttribute("aria-label", t("invite.seat", { n: entry.seat + 1, name: entry.name }));
      input.addEventListener("focus", function () { input.select(); });
      row.appendChild(input);

      const actions = el("div", "invite-row-actions");
      actions.appendChild(makeButton("btn", t("invite.copy"), "copy", link));
      if (navigator.share) actions.appendChild(makeButton("btn", t("invite.share"), "share", link));
      const qrButton = makeButton("btn", t("invite.qr"), "qr", link);
      qrButton.setAttribute("aria-label", t("invite.qrAlt"));
      qrButton.setAttribute("aria-expanded", "false");
      actions.appendChild(qrButton);
      row.appendChild(actions);

      const slot = el("div", "qr-slot");
      slot.hidden = true;
      row.appendChild(slot);
      els.inviteList.appendChild(row);
    });
    if (game.players.some(function (p) { return p.bot; })) {
      const row = el("div", "invite-row");
      const head = el("div", "invite-row-head");
      head.appendChild(el("span", null, t("setup.botChip")));
      row.appendChild(head);
      row.appendChild(el("p", "dialog-hint", t("invite.bot")));
      els.inviteList.appendChild(row);
    }
  }

  // A wrapped textarea shows the whole (long) invite URL; size it to its content.
  function fitInviteLinks() {
    els.inviteList.querySelectorAll("textarea.invite-link").forEach(function (input) {
      input.style.height = "auto";
      // scrollHeight excludes borders; height is border-box, so add them back.
      const style = window.getComputedStyle(input);
      const borders = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
      input.style.height = Math.max(input.scrollHeight + borders, 40) + "px";
    });
  }

  function renderQr(button) {
    const row = button.closest(".invite-row");
    if (!row) return;
    const slot = row.querySelector(".qr-slot");
    if (!slot) return;
    button.setAttribute("aria-expanded", slot.hidden ? "true" : "false");
    if (!slot.hidden) { slot.hidden = true; slot.textContent = ""; return; }
    try {
      const qr = window.qrcode(0, "M");
      qr.addData(button.getAttribute("data-value"));
      qr.make();
      const img = el("img");
      img.src = qr.createDataURL(4, 4);
      img.alt = t("invite.qrAlt");
      slot.appendChild(img);
      slot.hidden = false;
    } catch (_error) {
      toast(t("error.network"));
    }
  }

  // The toast would sit behind a modal dialog's backdrop, so invite feedback
  // lives inside the dialog instead.
  function setInviteFeedback(message) {
    els.inviteFeedback.textContent = message;
    if (inviteFeedbackTimer) clearTimeout(inviteFeedbackTimer);
    inviteFeedbackTimer = setTimeout(function () { els.inviteFeedback.textContent = ""; }, 3500);
  }

  function copyLink(link, button) {
    const row = button && button.closest(".invite-row");
    const field = row && row.querySelector("textarea.invite-link");
    function failed() {
      if (field) { field.focus(); field.select(); }
      setInviteFeedback(t("invite.copyFailed"));
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () {
        setInviteFeedback(t("invite.copied"));
        if (button) {
          const label = t("invite.copy");
          button.textContent = t("invite.copied");
          setTimeout(function () { button.textContent = label; }, 2000);
        }
      }).catch(failed);
      return;
    }
    failed();
  }

  function shareLink(link) {
    if (!navigator.share) return;
    navigator.share({ title: "Flop7", url: link }).catch(function () { /* user cancelled */ });
  }

  function openInvites() {
    if (!inviteSeats.length) return; // spectators and joined guests have no links
    renderInviteList();
    inviteSignature = inviteSignatureKey();
    if (inviteFeedbackTimer) { clearTimeout(inviteFeedbackTimer); inviteFeedbackTimer = null; }
    els.inviteFeedback.textContent = "";
    if (!els.inviteDialog.open) els.inviteDialog.showModal();
    fitInviteLinks();
  }

  // Let the opening deal play on the table before covering it with the dialog.
  function openInvitesAfterDeal() {
    const id = gameId;
    const count = game ? game.players.length : 2;
    const wait = DEAL_STEP_MS * (count - 1) + DEAL_DURATION_MS + 400;
    setTimeout(function () {
      if (gameId === id && game && !els.inviteDialog.open) openInvites();
    }, wait);
  }

  function openRules() {
    if (!els.rulesDialog.open) els.rulesDialog.showModal();
  }

  function showCard(value) {
    const text = String(value);
    const kind = /^\d+$/.test(text) ? "number" : text.startsWith("+") ? "plus"
      : text === "x2" ? "x2" : text;
    els.cardExplanation.textContent = t("card." + kind);
    if (!els.cardDialog.open) els.cardDialog.showModal();
  }

  // ---- live stream ----
  function closeStream() {
    if (stream) { try { stream.close(); } catch (_error) { /* ignore */ } stream = null; }
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    els.chip.hidden = true;
  }

  function connected() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    els.chip.hidden = true;
  }

  function disconnected() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      els.chip.hidden = false;
      reconnectTimer = null;
    }, 4000);
  }

  function connectStream() {
    closeStream();
    if (!gameId) return;
    stream = new EventSource("/api/games/" + encodeURIComponent(gameId) + "/events");
    stream.onopen = connected;
    stream.onmessage = function (event) {
      connected();
      let view;
      try { view = JSON.parse(event.data); } catch (_error) { return; }
      applyView(view);
    };
    stream.onerror = function () {
      disconnected();
      if (stream && stream.readyState === EventSource.CLOSED) {
        const id = gameId;
        setTimeout(function () {
          if (gameId === id && game && !stream) connectStream();
        }, 4000);
      }
    };
  }

  // ---- URL / bootstrap ----
  function parseUrl() {
    const params = new URLSearchParams(location.search);
    const id = params.get("game");
    const raw = location.hash.replace(/^#/, "");
    const frag = {};
    raw.split("&").forEach(function (pair) {
      if (!pair) return;
      const bits = pair.split("=");
      frag[decodeURIComponent(bits[0])] = decodeURIComponent(bits.slice(1).join("="));
    });
    let valid = false;
    if (id && /^\d+$/.test(frag.seat) && typeof frag.token === "string" &&
        /^[A-Za-z0-9_-]{16,}$/.test(frag.token)) {
      const seat = Number(frag.seat);
      if (seat >= 0 && seat <= 5) {
        valid = true;
        frag.seat = seat;
      }
    }
    // Fragments must never linger in the address bar (spec 5).
    if (raw) {
      history.replaceState(null, "", location.pathname + (id ? "?game=" + encodeURIComponent(id) : ""));
    }
    return { id: id, fragment: valid ? frag : null };
  }

  async function loadGame() {
    if (!gameId) return;
    loading = true;
    render();
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId));
      applyView(view);
      connectStream();
    } catch (err) {
      toast(translateError(err.detail));
      tokens = {};
      inviteSeats = [];
      game = null;
    } finally {
      loading = false;
      render();
    }
  }

  async function bootstrap() {
    I18n.apply(document);
    render();
    const url = parseUrl();
    if (url.id && url.fragment) {
      tokens = {};
      tokens[url.fragment.seat] = url.fragment.token;
      mySeat = url.fragment.seat;
      hotseat = false;
      inviteSeats = [];
      gameId = url.id;
      saveGameData(); // persist the joined seat for reloads (spec 6.7)
      await loadGame();
    } else if (url.id) {
      const saved = loadGameData(url.id);
      if (saved) {
        tokens = saved.tokens;
        mySeat = saved.mySeat;
        hotseat = saved.hotseat;
        inviteSeats = saved.invites;
      } else {
        tokens = {};
        mySeat = 0;
        hotseat = false;
        inviteSeats = [];
      }
      gameId = url.id;
      await loadGame();
    } else {
      const setup = Storage.get("setup");
      if (setup && typeof setup === "object") {
        if (typeof setup.names === "string") els.names.value = setup.names;
        els.addBot.checked = setup.bot === true;
        els.hotseat.checked = setup.hotseat !== false;
      }
      updatePreview();
    }
  }

  // ---- wiring ----
  els.form.addEventListener("submit", createGame);
  els.names.addEventListener("input", function () { clearSetupError(); updatePreview(); });
  els.addBot.addEventListener("change", function () { clearSetupError(); updatePreview(); });

  document.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.getAttribute("data-action");
    if (action === "start") {
      if (els.form.requestSubmit) els.form.requestSubmit();
      else createGame({ preventDefault: function () {} });
    } else if (action === "hit" || action === "stay" || action === "next") doMove(action);
    else if (action === "target") doMove("target", Number(button.getAttribute("data-value")));
    else if (action === "rematch") rematch();
    else if (action === "invite") openInvites();
    else if (action === "copy") copyLink(button.getAttribute("data-value"), button);
    else if (action === "share") shareLink(button.getAttribute("data-value"));
    else if (action === "qr") renderQr(button);
  });

  els.players.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-card]");
    if (button) showCard(button.getAttribute("data-card"));
  });

  els.btnRules.addEventListener("click", openRules);
  els.btnLanguage.addEventListener("click", function () { I18n.cycleLanguage(); });
  els.btnTheme.addEventListener("click", function () { Theme.cycle(); });
  els.btnNewGame.addEventListener("click", leaveGame);
  els.btnSound.addEventListener("click", function () {
    soundOn = !soundOn;
    Storage.set("sound", soundOn);
    renderTopbar();
    if (soundOn) beep("draws");
  });

  document.querySelectorAll(".dialog-close").forEach(function (button) {
    button.addEventListener("click", function () { button.closest("dialog").close(); });
  });
  $("invite-close").addEventListener("click", function () { els.inviteDialog.close(); });

  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  });
  window.addEventListener("resize", function () {
    if (els.inviteDialog.open) fitInviteLinks();
  });

  document.addEventListener("flop7:languagechange", function () { render(); });
  document.addEventListener("flop7:themechange", function () { renderTopbar(); });

  document.addEventListener("keydown", function (event) {
    const target = event.target || {};
    const tag = (target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable) return;
    if (document.querySelector("dialog[open]")) return;
    if (event.key === "?") { event.preventDefault(); openRules(); return; }
    if (busy) return;
    if (event.key === " " && tag === "button") return; // keep native button activation
    if (event.key === " ") {
      if (canHitOrStay()) { event.preventDefault(); doMove("hit"); }
      else if (game && game.round_over && game.winner === null && anyControl()) {
        event.preventDefault();
        doMove("next");
      }
    } else if (event.key === "h" || event.key === "H") {
      if (canHitOrStay()) { event.preventDefault(); doMove("hit"); }
    } else if (event.key === "s" || event.key === "S") {
      if (canHitOrStay()) { event.preventDefault(); doMove("stay"); }
    } else if (/^[1-6]$/.test(event.key)) {
      const index = Number(event.key) - 1;
      if (canTarget() && index < game.targets.length) {
        event.preventDefault();
        doMove("target", game.targets[index]);
      }
    }
  });

  window.addEventListener("beforeunload", function (event) {
    if (game && game.winner === null && anyControl()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("pagehide", closeStream);

  bootstrap();
})();
