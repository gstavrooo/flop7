/* Flop7 pre-paint boot: namespaced, defensive storage + theme application.
 * Loaded synchronously in <head> so the stored theme (and language) are applied
 * before first paint. All storage access is wrapped: blocked or corrupt storage
 * degrades to session-only without breaking the game (spec 5, 6.7).
 */
(function () {
  "use strict";

  const PREFIX = "flop7:v1:";
  let available = true;

  try {
    const probe = PREFIX + "probe";
    localStorage.setItem(probe, "1");
    if (localStorage.getItem(probe) !== "1") available = false;
    localStorage.removeItem(probe);
  } catch (_error) {
    available = false;
  }

  const Storage = Object.freeze({
    available: function () { return available; },
    get: function (key) {
      if (!available) return null;
      try {
        const raw = localStorage.getItem(PREFIX + key);
        if (raw === null) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" && "value" in parsed ? parsed.value : null;
      } catch (_error) {
        return null; // corrupt entry: ignore it, never block startup
      }
    },
    set: function (key, value) {
      if (!available) return false;
      try {
        localStorage.setItem(PREFIX + key, JSON.stringify({ value: value }));
        return true;
      } catch (_error) {
        available = false;
        return false;
      }
    },
    remove: function (key) {
      if (!available) return;
      try { localStorage.removeItem(PREFIX + key); } catch (_error) { available = false; }
    }
  });

  window.Flop7Storage = Storage;

  const MODES = ["auto", "light", "dark"];
  let mode = Storage.get("theme");
  if (MODES.indexOf(mode) === -1) mode = "auto";

  function apply(next) {
    mode = next;
    if (next === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", next);
    Storage.set("theme", next);
    document.dispatchEvent(new CustomEvent("flop7:themechange", { detail: { theme: next } }));
  }

  function cycle() {
    apply(mode === "auto" ? "light" : mode === "light" ? "dark" : "auto");
  }

  // The language attribute is set here too so it is correct before first paint;
  // i18n.js owns the dictionary and re-applies it when the document is ready.
  const lang = Storage.get("language");
  if (lang === "en" || lang === "de") document.documentElement.lang = lang;

  if (mode !== "auto") document.documentElement.setAttribute("data-theme", mode);

  window.Flop7Theme = Object.freeze({
    get: function () { return mode; },
    set: apply,
    cycle: cycle
  });
})();
