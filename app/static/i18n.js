/* Flop7 i18n — every visible UI string lives here (spec 6.9).
 * `en` and `de` must have exactly the same key set; tests/test_i18n.py
 * verifies parity and that every API error message from engine.py is present.
 */
(function () {
  "use strict";

  const EN = Object.freeze({
    "tagline": "Push your luck. Draw, bank, win.",
    "nav.rules": "Rules",
    "nav.language": "Language",
    "nav.soundOn": "Sound on",
    "nav.soundOff": "Sound off",
    "nav.themeAuto": "Theme: automatic",
    "nav.themeLight": "Theme: light",
    "nav.themeDark": "Theme: dark",
    "nav.toolbar": "Toolbar",
    "language.en": "English",
    "language.de": "German",

    "setup.title": "New game",
    "table.aria": "Game table",
    "dock.aria": "Controls",
    "setup.namesLabel": "Players (comma-separated)",
    "setup.namesPlaceholder": "Ann, Bob, Chris",
    "setup.botChip": "🤖 Bot",
    "setup.hotseat": "Hot-seat (one device)",
    "setup.addBot": "Add a bot",
    "setup.start": "Start game",
    "setup.hint": "2 to 6 players including the bot. One device can play every seat.",
    "setup.previewOne": "{names} — 1 player",
    "setup.previewMany": "{names} — {count} players",
    "setup.errorEmpty": "Enter at least two players.",
    "setup.errorTooMany": "At most 6 players including the bot.",
    "setup.errorName": "Each name must be 1 to 20 characters.",

    "dock.setup": "Set up a game to begin",
    "dock.round": "Round {round}",
    "dock.deck": "Deck: {count}",
    "dock.yourTurn": "Your turn — hit or stay!",
    "dock.thinking": "{name} is thinking …",
    "dock.waiting": "Waiting for {name} …",
    "dock.target": "Choose a target for {card}:",
    "dock.targetChoice": "Target {n}: {name}",
    "dock.spectator": "You are watching this game.",
    "dock.break": "Round over — start the next one.",

    "table.total": "Total: {total} / 200",
    "table.score": "Round: {score}",
    "table.numbers": "{count} / 7 unique numbers",
    "table.second": "Second Chance ready",
    "table.you": "you",
    "table.dealHint": "Opening deal: every player starts with one face-up card.",
    "card.label": "Card: {card}",
    "status.active": "active",
    "status.stayed": "stayed",
    "status.bust": "bust",
    "status.thinking": "thinking …",
    "status.finished": "round finished",
    "newCard": "New card: {card}",

    "action.hit": "Hit",
    "action.stay": "Stay",
    "action.next": "Next round",
    "action.rematch": "Rematch",
    "action.invite": "Invite",

    "round.title": "Round {round} finished",
    "round.result": "{name}: {points} points",
    "round.flop7": "Flip 7 bonus!",
    "winner.title": "{name} wins!",
    "winner.margin": "by {margin} points",
    "tie.title": "It's a tie — another round!",

    "connection.lost": "Connection lost — reconnecting …",
    "connection.waking": "Waking up the table — the first request can take a minute …",
    "action.tryAgain": "Try again",
    "storage.unavailable": "Browser storage is blocked — invites and settings last only until this tab closes.",
    "toast.closed": "Close",

    "invite.title": "Invite players",
    "invite.intro": "Send each link to its player. The secret stays in the link fragment.",
    "invite.self": "This device",
    "invite.bot": "Bot — no invite needed",
    "invite.seat": "Seat {n}: {name}",
    "invite.copy": "Copy",
    "invite.copied": "Link copied",
    "invite.copyFailed": "Copy failed — select the link and copy it manually.",
    "invite.share": "Share",
    "invite.qr": "QR",
    "invite.qrAlt": "QR code for this invite link",
    "invite.close": "Done",

    "rules.title": "How to play",
    "rules.turnTitle": "Your turn",
    "rules.turn": "Hit to draw one card, or stay to bank your score. Turns pass card by card.",
    "rules.duplicateTitle": "Duplicate numbers",
    "rules.duplicate": "Drawing a number you already hold busts the round (0 points) unless a Second Chance protects you.",
    "rules.modifiersTitle": "Modifiers",
    "rules.modifiers": "+N adds N points at once. x2 doubles your numbers before +N is added.",
    "rules.flop7Title": "Flip 7",
    "rules.flop7": "Hold 7 different numbers to score +15 and end the round for everyone immediately.",
    "rules.actionsTitle": "Action cards",
    "rules.actions": "Freeze banks a target's score and sits them out. Flip Three lets a target draw up to three cards. Second Chance saves one bust. Action cards drawn during Flip Three are played by the target afterwards.",
    "rules.targetTitle": "Targets",
    "rules.target": "Freeze, Flip Three and Second Chance may ask you to pick a target — choose one of the highlighted seats.",
    "rules.roundTitle": "End of a round",
    "rules.round": "When everyone is finished, round scores are added to totals. Any seat token holder can start the next round.",
    "rules.winTitle": "Winning",
    "rules.win": "First to 200+ points and sole leader wins. A tie at 200+ plays another round.",
    "card.title": "Card explanation",
    "card.number": "A number adds its value to your score. Drawing it twice busts you unless you have a Second Chance.",
    "card.plus": "+N adds N points to your round score right away.",
    "card.x2": "x2 doubles the sum of your numbers; +N modifiers are added afterwards.",
    "card.freeze": "Freeze makes the chosen target bank their score and sit out the rest of the round.",
    "card.flip3": "Flip Three makes the chosen target draw up to three cards; action cards drawn are played afterwards.",
    "card.second": "Second Chance saves you from one duplicate number this round, then it is discarded.",

    "leave.confirm": "Leave this unfinished game?",
    "error.network": "Cannot reach the server.",
    "error.gameOver": "game is over",
    "error.startNextRound": "start the next round first",
    "error.roundRunning": "round is still running",
    "error.notYourTurn": "not your turn",
    "error.chooseTarget": "choose a target first",
    "error.nothingToTarget": "nothing to target",
    "error.notYourDecision": "not your decision",
    "error.invalidTarget": "invalid target",
    "error.noCardsLeft": "no cards left",
    "error.tooManyAutomatic": "too many automatic moves",
    "error.badPlayerCount": "a game needs 2 to 6 players including the optional bot",
    "error.tokenRequired": "this device does not control that seat",
    "error.gameNotFound": "game not found",
    "error.staleWrite": "game changed in the meantime, reload and try again",
    "error.badNames": "each name must be 1 to 20 characters after trimming",
    "error.invalidRequest": "invalid request"
  });

  const DE = Object.freeze({
    "tagline": "Zieh, sichere, gewinn.",
    "nav.rules": "Regeln",
    "nav.language": "Sprache",
    "nav.soundOn": "Ton an",
    "nav.soundOff": "Ton aus",
    "nav.themeAuto": "Design: automatisch",
    "nav.themeLight": "Design: hell",
    "nav.themeDark": "Design: dunkel",
    "nav.toolbar": "Werkzeugleiste",
    "language.en": "Englisch",
    "language.de": "Deutsch",

    "setup.title": "Neues Spiel",
    "table.aria": "Spieltisch",
    "dock.aria": "Steuerung",
    "setup.namesLabel": "Spieler (mit Komma getrennt)",
    "setup.namesPlaceholder": "Ann, Bob, Chris",
    "setup.botChip": "🤖 Bot",
    "setup.hotseat": "Hotseat (ein Gerät)",
    "setup.addBot": "Bot hinzufügen",
    "setup.start": "Spiel starten",
    "setup.hint": "2 bis 6 Spieler inklusive Bot. Ein Gerät kann alle Plätze spielen.",
    "setup.previewOne": "{names} — 1 Spieler",
    "setup.previewMany": "{names} — {count} Spieler",
    "setup.errorEmpty": "Gib mindestens zwei Spieler ein.",
    "setup.errorTooMany": "Maximal 6 Spieler inklusive Bot.",
    "setup.errorName": "Jeder Name muss 1 bis 20 Zeichen lang sein.",

    "dock.setup": "Richte ein Spiel ein, um zu beginnen",
    "dock.round": "Runde {round}",
    "dock.deck": "Stapel: {count}",
    "dock.yourTurn": "Du bist dran — ziehen oder bleiben.",
    "dock.thinking": "{name} überlegt …",
    "dock.waiting": "Warten auf {name} …",
    "dock.target": "Wähle ein Ziel für {card}:",
    "dock.targetChoice": "Ziel {n}: {name}",
    "dock.spectator": "Du schaust diesem Spiel zu.",
    "dock.break": "Runde vorbei — starte die nächste.",

    "table.total": "Gesamt: {total} / 200",
    "table.score": "Runde: {score}",
    "table.numbers": "{count} / 7 verschiedene Zahlen",
    "table.second": "Second Chance bereit",
    "table.you": "du",
    "table.dealHint": "Erstverteilung: Jeder Spieler beginnt mit einer offenen Karte.",
    "card.label": "Karte: {card}",
    "status.active": "aktiv",
    "status.stayed": "ausgestiegen",
    "status.bust": "verzockt",
    "status.thinking": "überlegt …",
    "status.finished": "Runde beendet",
    "newCard": "Neue Karte: {card}",

    "action.hit": "Ziehen",
    "action.stay": "Bleiben",
    "action.next": "Nächste Runde",
    "action.rematch": "Revanche",
    "action.invite": "Einladen",

    "round.title": "Runde {round} beendet",
    "round.result": "{name}: {points} Punkte",
    "round.flop7": "Flip 7-Bonus!",
    "winner.title": "{name} gewinnt!",
    "winner.margin": "mit {margin} Punkten Vorsprung",
    "tie.title": "Unentschieden — eine weitere Runde!",

    "connection.lost": "Verbindung verloren — erneuter Versuch …",
    "connection.waking": "Der Tisch wird geweckt — die erste Anfrage kann eine Minute dauern …",
    "action.tryAgain": "Erneut versuchen",
    "storage.unavailable": "Browser-Speicher blockiert — Einladungen und Einstellungen gelten nur bis zum Schließen dieses Tabs.",
    "toast.closed": "Schließen",

    "invite.title": "Spieler einladen",
    "invite.intro": "Sende jeden Link an seinen Spieler. Das Geheimnis bleibt im Link-Fragment.",
    "invite.self": "Dieses Gerät",
    "invite.bot": "Bot — keine Einladung nötig",
    "invite.seat": "Platz {n}: {name}",
    "invite.copy": "Kopieren",
    "invite.copied": "Link kopiert",
    "invite.copyFailed": "Kopieren fehlgeschlagen — wähle den Link aus und kopiere ihn manuell.",
    "invite.share": "Teilen",
    "invite.qr": "QR",
    "invite.qrAlt": "QR-Code für diesen Einladungslink",
    "invite.close": "Fertig",

    "rules.title": "Spielanleitung",
    "rules.turnTitle": "Dein Zug",
    "rules.turn": "Ziehen nimmt eine Karte, Bleiben sichert deine Punkte. Die Plätze wechseln Karte für Karte.",
    "rules.duplicateTitle": "Doppelte Zahlen",
    "rules.duplicate": "Ziehst du eine Zahl, die du schon hast, bist du verzockt (0 Punkte) — außer eine Second Chance schützt dich.",
    "rules.modifiersTitle": "Modifikatoren",
    "rules.modifiers": "+N addiert sofort N Punkte. x2 verdoppelt deine Zahlen, bevor +N addiert wird.",
    "rules.flop7Title": "Flip 7",
    "rules.flop7": "Halte 7 verschiedene Zahlen für +15 Punkte — die Runde endet sofort für alle.",
    "rules.actionsTitle": "Aktionskarten",
    "rules.actions": "Freeze sichert die Punkte eines Ziels und lässt es aussteigen. Flip Three lässt ein Ziel bis zu drei Karten ziehen. Second Chance rettet vor einem Verzocken. Aktionskarten aus Flip Three spielt das Ziel danach.",
    "rules.targetTitle": "Ziele",
    "rules.target": "Freeze, Flip Three und Second Chance können ein Ziel verlangen — wähle einen der markierten Plätze.",
    "rules.roundTitle": "Rundenende",
    "rules.round": "Wenn alle fertig sind, kommen die Rundenpunkte zu den Gesamtpunkten. Jeder Inhaber eines Platz-Tokens kann die nächste Runde starten.",
    "rules.winTitle": "Gewinnen",
    "rules.win": "Wer zuerst 200+ Punkte hat und allein führt, gewinnt. Bei Gleichstand ab 200 gibt es eine weitere Runde.",
    "card.title": "Kartenerklärung",
    "card.number": "Eine Zahl addiert ihren Wert zu deinen Punkten. Ziehst du sie doppelt, bist du verzockt — außer mit Second Chance.",
    "card.plus": "+N addiert sofort N Punkte zu deinen Rundenpunkten.",
    "card.x2": "x2 verdoppelt die Summe deiner Zahlen; +N-Modifikatoren kommen danach dazu.",
    "card.freeze": "Freeze lässt das gewählte Ziel seine Punkte sichern und für den Rest der Runde aussteigen.",
    "card.flip3": "Flip Three lässt das gewählte Ziel bis zu drei Karten ziehen; gezogene Aktionskarten werden danach gespielt.",
    "card.second": "Second Chance rettet dich in dieser Runde vor einer doppelten Zahl, dann wird sie abgelegt.",

    "leave.confirm": "Dieses unfertige Spiel verlassen?",
    "error.network": "Server nicht erreichbar.",
    "error.gameOver": "das Spiel ist vorbei",
    "error.startNextRound": "zuerst die nächste Runde starten",
    "error.roundRunning": "die Runde läuft noch",
    "error.notYourTurn": "du bist nicht dran",
    "error.chooseTarget": "zuerst ein Ziel wählen",
    "error.nothingToTarget": "kein Ziel vorhanden",
    "error.notYourDecision": "nicht deine Entscheidung",
    "error.invalidTarget": "ungültiges Ziel",
    "error.noCardsLeft": "keine Karten mehr",
    "error.tooManyAutomatic": "zu viele automatische Züge",
    "error.badPlayerCount": "ein Spiel braucht 2 bis 6 Spieler inklusive des optionalen Bots",
    "error.tokenRequired": "dieses Gerät steuert diesen Platz nicht",
    "error.gameNotFound": "Spiel nicht gefunden",
    "error.staleWrite": "das Spiel hat sich inzwischen geändert — neu laden und erneut versuchen",
    "error.badNames": "jeder Name muss nach dem Trimmen 1 bis 20 Zeichen lang sein",
    "error.invalidRequest": "ungültige Anfrage"
  });

  const DICTS = Object.freeze({ en: EN, de: DE });
  let current = "en";

  function detect() {
    const stored = window.Flop7Storage && window.Flop7Storage.get("language");
    if (stored === "en" || stored === "de") return stored;
    const nav = (navigator.language || "").toLowerCase();
    return nav.startsWith("de") ? "de" : "en";
  }

  function t(key, vars) {
    const dict = DICTS[current] || EN;
    let text = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        text = text.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return text;
  }

  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
    });
  }

  function setLanguage(lang) {
    if (!DICTS[lang]) return;
    current = lang;
    document.documentElement.lang = lang;
    if (window.Flop7Storage) window.Flop7Storage.set("language", lang);
    apply(document);
    document.dispatchEvent(new CustomEvent("flop7:languagechange", { detail: { language: lang } }));
  }

  function cycleLanguage() {
    setLanguage(current === "en" ? "de" : "en");
  }

  current = detect();
  document.documentElement.lang = current;

  window.Flop7I18n = Object.freeze({
    t: t,
    apply: apply,
    setLanguage: setLanguage,
    cycleLanguage: cycleLanguage,
    getLanguage: function () { return current; },
    dictionaries: DICTS
  });
})();
