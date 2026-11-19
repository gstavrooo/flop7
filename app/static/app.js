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
  let lastEventId = 0;
  let busy = false;
  let soundOn = Storage.get("sound") !== false;
  let audioCtx = null;

  const $ = function (id) { return document.getElementById(id); };
  const els = {
    setupScreen: $("setup-screen"), tableScreen: $("table-screen"),
    form: $("setup-form"), names: $("names"), botChip: $("bot-chip"),
    addBot: $("add-bot"), hotseat: $("hotseat"), preview: $("participant-preview"),
    setupError: $("setup-error"), btnStart: $("btn-start"),
    players: $("players"), winnerBanner: $("winner-banner"),
    winnerTitle: $("winner-title"), winnerMargin: $("winner-margin"),
    summary: $("round-summary"), summaryTitle: $("round-summary-title"),
    results: $("round-results"),
    dockRound: $("dock-round"), dockDeck: $("dock-deck"),
    dockPrompt: $("dock-prompt"), dockActions: $("dock-actions"),
    waitingFill: $("waiting-fill"),
    toast: $("toast"), chip: $("connection-chip"), storageHint: $("storage-hint"),
    btnRules: $("btn-rules"), btnLanguage: $("btn-language"),
    btnSound: $("btn-sound"), btnTheme: $("btn-theme"),
    inviteDialog: $("invite-dialog"), inviteList: $("invite-list"),
    rulesDialog: $("rules-dialog"), cardDialog: $("card-dialog"),
    cardExplanation: $("card-explanation")
  };

  // ---- small helpers ----
  function maxId(events) {
    const ids = (events || []).map(function (e) { return e && e.id; })
      .filter(function (n) { return typeof n === "number"; });
    return ids.length ? Math.max.apply(null, ids) : 0;
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.hidden = true; }, 3500);
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

  // ---- network ----
  async function api(path, options) {
    options = options || {};
    const headers = Object.assign({}, options.headers || {});
    if (options.body) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, Object.assign({}, options, { headers: headers }));
    } catch (_error) {
      throw { status: 0, detail: t("error.network") };
    }
    let data = null;
    try { data = await response.json(); } catch (_error) { data = null; }
    if (!response.ok) {
      throw { status: response.status, detail: (data && data.detail) || ("HTTP " + response.status) };
    }
    return data;
  }

  async function reloadGame() {
    if (!gameId) return;
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId));
      applyView(view);
    } catch (err) {
      toast(translateError(err.detail));
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
  function render() {
    I18n.apply(document);
    els.storageHint.hidden = Storage.available();
    const inGame = game !== null;
    els.setupScreen.hidden = inGame;
    els.tableScreen.hidden = !inGame;
    renderTopbar();
    updatePreview();
    if (inGame) {
      renderPlayers();
      renderSummary();
      renderWinner();
    } else {
      els.players.textContent = "";
      els.summary.hidden = true;
      els.winnerBanner.hidden = true;
    }
    renderDock();
    updateTitle();
    if (els.inviteDialog.open) renderInviteList();
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

  function renderPlayers() {
    els.players.textContent = "";
    game.players.forEach(function (player, seat) {
      const card = el("div", "player-card");
      if (!game.round_over && game.winner === null && seat === game.turn) card.classList.add("is-turn");
      if (!hotseat && seat === mySeat) card.classList.add("is-mine");

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

      if (player.second) card.appendChild(el("span", "second-flag", t("table.second")));

      const status = statusFor(player, seat);
      if (status) card.appendChild(el("span", "status-badge " + status.cls, t(status.key)));

      const newest = newestCardFor(seat);
      const cards = el("div", "cards");
      player.numbers.concat(player.mods).forEach(function (value) {
        const kind = typeof value === "number" ? "number" : value.startsWith("+") ? "plus" : value === "x2" ? "x2" : "action";
        const button = el("button", "card card-" + kind + (value === newest ? " card-new" : ""), String(value));
        button.type = "button";
        button.setAttribute("data-card", String(value));
        button.setAttribute("aria-label", t("newCard", { card: String(value) }));
        cards.appendChild(button);
      });
      card.appendChild(cards);
      els.players.appendChild(card);
    });
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

  function setWaiting(level) {
    els.waitingFill.className = "waiting-fill w-" + level;
  }

  function renderDock() {
    els.dockActions.textContent = "";
    if (!game) {
      els.dockRound.setAttribute("data-i18n", "dock.setup");
      els.dockRound.textContent = t("dock.setup");
      els.dockPrompt.setAttribute("data-i18n", "dock.setup");
      els.dockPrompt.textContent = t("dock.setup");
      els.dockDeck.textContent = "";
      setWaiting("none");
      return;
    }
    els.dockRound.removeAttribute("data-i18n");
    els.dockPrompt.removeAttribute("data-i18n");
    els.dockRound.textContent = t("dock.round", { round: game.round });
    els.dockDeck.textContent = t("dock.deck", { count: game.deck_count });

    if (game.winner !== null) {
      els.dockPrompt.textContent = t("winner.title", { name: game.players[game.winner].name });
      if (anyControl()) els.dockActions.appendChild(makeButton("btn-next", t("action.rematch"), "rematch"));
      els.dockActions.appendChild(makeButton("btn", t("action.invite"), "invite"));
      setWaiting("ready");
      return;
    }

    if (game.round_over) {
      els.dockPrompt.textContent = t("dock.break");
      if (anyControl()) els.dockActions.appendChild(makeButton("btn-next", t("action.next"), "next"));
      els.dockActions.appendChild(makeButton("btn", t("action.invite"), "invite"));
      setWaiting("ready");
      return;
    }

    if (game.pending && game.pending.length) {
      const pending = game.pending[0];
      const kind = pending.kind === "give" ? "second" : pending.kind;
      els.dockPrompt.textContent = t("dock.target", { card: kind });
      if (controls(pending.by)) {
        game.targets.forEach(function (seat, index) {
          els.dockActions.appendChild(makeButton(
            "btn-target", t("dock.targetChoice", { n: index + 1, name: game.players[seat].name }),
            "target", seat
          ));
        });
        setWaiting("ready");
      } else {
        setWaiting("wait");
      }
      return;
    }

    if (isBotTurn()) {
      els.dockPrompt.textContent = t("dock.thinking", { name: game.players[game.turn].name });
      setWaiting("think");
      return;
    }

    if (controls(game.turn)) {
      els.dockPrompt.textContent = t("dock.yourTurn");
      els.dockActions.appendChild(makeButton("btn-hit", t("action.hit"), "hit"));
      els.dockActions.appendChild(makeButton("btn-stay", t("action.stay"), "stay"));
      setWaiting("ready");
    } else {
      els.dockPrompt.textContent = t("dock.waiting", { name: game.players[game.turn].name });
      setWaiting("wait");
    }
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
      body: JSON.stringify(body)
    };
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId) + "/" + action, options);
      applyView(view);
    } catch (err) {
      toast(translateError(err.detail));
      if (err.status === 409) await reloadGame();
    } finally {
      busy = false;
      render();
    }
  }

  async function startNewGame(names, withBot) {
    const view = await api("/api/games", {
      method: "POST", body: JSON.stringify({ players: names, bot: withBot })
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
    return view;
  }

  async function createGame(event) {
    event.preventDefault();
    const names = els.names.value.split(",").map(function (n) { return n.trim(); }).filter(Boolean);
    const withBot = els.addBot.checked;
    if (names.length + Number(withBot) < 2) { setSetupError(t("setup.errorEmpty")); return; }
    if (names.length + Number(withBot) > 6) { setSetupError(t("setup.errorTooMany")); return; }
    if (names.some(function (n) { return n.length < 1 || n.length > 20; })) {
      setSetupError(t("setup.errorName"));
      return;
    }
    clearSetupError();
    mySeat = 0;
    hotseat = els.hotseat.checked;
    try {
      await startNewGame(names, withBot);
      Storage.set("setup", { names: names.join(", "), bot: withBot, hotseat: hotseat });
      openInvites();
    } catch (err) {
      setSetupError(translateError(err.detail));
    }
  }

  async function rematch() {
    if (!game || busy) return;
    busy = true;
    const names = game.players.filter(function (p) { return !p.bot; })
      .map(function (p) { return p.name; });
    const withBot = game.players.some(function (p) { return p.bot; });
    try {
      await startNewGame(names, withBot); // mySeat + hotseat stay the same
      toast(t("invite.title"));
    } catch (err) {
      toast(translateError(err.detail));
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
      const input = el("input");
      input.type = "text";
      input.readOnly = true;
      input.value = link;
      input.setAttribute("aria-label", t("invite.seat", { n: entry.seat + 1, name: entry.name }));
      row.appendChild(input);

      const actions = el("div", "invite-row-actions");
      actions.appendChild(makeButton("btn", t("invite.copy"), "copy", link));
      if (navigator.share) actions.appendChild(makeButton("btn", t("invite.share"), "share", link));
      const qrButton = makeButton("btn", t("invite.qr"), "qr", link);
      qrButton.setAttribute("aria-label", t("invite.qrAlt"));
      actions.appendChild(qrButton);
      row.appendChild(actions);

      const slot = el("div", "qr-slot");
      slot.hidden = true;
      row.appendChild(slot);
      els.inviteList.appendChild(row);
    });
  }

  function renderQr(button) {
    const row = button.closest(".invite-row");
    if (!row) return;
    const slot = row.querySelector(".qr-slot");
    if (!slot) return;
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

  function copyLink(link) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () { toast(t("invite.copied")); })
        .catch(function () { toast(t("invite.copied")); });
      return;
    }
    toast(t("invite.copied"));
  }

  function shareLink(link) {
    if (!navigator.share) return;
    navigator.share({ title: "Flop7", url: link }).catch(function () { /* user cancelled */ });
  }

  function openInvites() {
    renderInviteList();
    if (!els.inviteDialog.open) els.inviteDialog.showModal();
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
    try {
      const view = await api("/api/games/" + encodeURIComponent(gameId));
      applyView(view);
      connectStream();
    } catch (err) {
      toast(translateError(err.detail));
      tokens = {};
      inviteSeats = [];
      game = null;
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
      saveGameData();
      gameId = url.id;
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
  els.names.addEventListener("input", updatePreview);
  els.addBot.addEventListener("change", updatePreview);

  document.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.getAttribute("data-action");
    if (action === "hit" || action === "stay" || action === "next") doMove(action);
    else if (action === "target") doMove("target", Number(button.getAttribute("data-value")));
    else if (action === "rematch") rematch();
    else if (action === "invite") openInvites();
    else if (action === "copy") copyLink(button.getAttribute("data-value"));
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
    if (game && game.winner === null) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("pagehide", closeStream);

  bootstrap();
})();
