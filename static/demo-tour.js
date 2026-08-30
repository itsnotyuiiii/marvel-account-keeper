/* Automated guided tour.
 *
 * Dormant unless the page is opened with ?demo in the URL. When active it
 * BOTH drives the app on its own (unlock, switch views, open a card, refresh,
 * flip the theme, quack) AND shows a looping captioned subtitle track with a
 * top progress bar — so the whole thing can simply be screen-recorded as a
 * narrated ~30-second demo. No clicking required.
 *
 * The caption overlay is pointer-events:none; the driving is done by
 * dispatching real clicks on the app's own elements.
 */
(function () {
  "use strict";
  if (!/[?&]demo(\b|=)/.test(location.search)) return;

  var DEMO_PASSWORD = "demo1234";

  // ── caption track — 7 beats, loops every TOTAL ms ──────────────────────────
  var BEATS = [
    { tag: "Marvel Rivals Account Tracker",
      text: "A local, encrypted vault for all your Marvel Rivals accounts." },
    { tag: "Cards view",
      text: "Every account at a glance — rank-colored, with neon borders on your mains." },
    { tag: "Full details",
      text: "Open any card for credentials, ranks, notes, and one-click copy." },
    { tag: "Table & Ladder",
      text: "Switch to a dense sortable table, or a ladder grouped by rank tier." },
    { tag: "Live Rivals stats",
      text: "Refresh ranks from Tracker.gg, then open full profiles on RivalsData." },
    { tag: "Make it yours",
      text: "Flip between a light and dark theme — your layout, remembered." },
    { tag: "Private & offline",
      text: "Passwords stay local, protected with authenticated AES-256 encryption." }
  ];
  var TOTAL = 32000;
  var BEAT_MS = TOTAL / BEATS.length;

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function $(s) { return document.querySelector(s); }
  function $all(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function click(el) { if (el) el.click(); }
  function setInput(el, val) {
    if (!el) return;
    var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
    desc.set.call(el, val);                       // bypass React's value lock
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function viewBtn(name) {
    return $all(".app-viewseg-btn").filter(function (b) {
      return b.textContent.toLowerCase().indexOf(name) >= 0;
    })[0];
  }
  function cardByName(name) {
    return $all(".rcard").filter(function (c) {
      return c.textContent.indexOf(name) >= 0;
    })[0];
  }
  function themeBtn() { return $('.app-btn-icon[aria-label*="mode"]'); }

  // ── timed actions that drive the app (ms from loop start) ──────────────────
  var ACTIONS = [
    { at: 600,   fn: function () { setInput($('.lock-form input[type="password"]'), DEMO_PASSWORD); } },
    { at: 1900,  fn: function () { click($(".lock-btn")); } },
    { at: 9300,  fn: function () { click($(".rcard")); } },
    { at: 13000, fn: function () { click($(".drawer-x")); } },
    { at: 13900, fn: function () { click(viewBtn("table")); } },
    { at: 16300, fn: function () { click(viewBtn("ladder")); } },
    { at: 18500, fn: function () { click(viewBtn("cards")); } },
    { at: 20200, fn: function () {
        var c = cardByName("HoldThisAcorn") || $(".rcard");
        click(c && c.querySelector(".refresh-btn"));
    } },
    { at: 23300, fn: function () { click(themeBtn()); } },          // → light
    { at: 28000, fn: function () { click($(".app-duck")); } },      // quack
    { at: 30400, fn: function () { click(themeBtn()); } },          // → dark
    { at: 31200, fn: function () { click($(".app-btn-ghost:not(.app-btn-refresh-all)")); } } // lock
  ];

  // ── overlay styles ─────────────────────────────────────────────────────────
  var CSS = [
    "#demo-progress{position:fixed;left:0;top:0;height:3px;width:0;z-index:99998;",
      "pointer-events:none;background:linear-gradient(90deg,#36e0ff,#7a5cff);",
      "box-shadow:0 0 10px #36e0ff,0 0 4px #7a5cff}",
    "#demo-cap{position:fixed;left:50%;bottom:36px;transform:translateX(-50%);",
      "z-index:99999;pointer-events:none;max-width:660px;width:calc(100vw - 56px);",
      "box-sizing:border-box;padding:16px 26px;border-radius:14px;text-align:center;",
      "background:rgba(11,13,20,.95);border:2px solid #36e0ff;",
      "box-shadow:0 0 24px rgba(54,224,255,.6),0 0 58px rgba(122,92,255,.4),",
        "inset 0 0 22px rgba(54,224,255,.14)}",
    "#demo-cap.demo-in{animation:demo-pop .45s cubic-bezier(.2,.7,.3,1.12) both}",
    "@keyframes demo-pop{from{opacity:0;transform:translateX(-50%) translateY(16px)}",
      "to{opacity:1;transform:translateX(-50%) translateY(0)}}",
    "#demo-cap .demo-tag{font-family:ui-monospace,Menlo,Consolas,monospace;",
      "font-size:11px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;",
      "color:#5ce6ff;margin-bottom:6px;text-shadow:0 0 8px rgba(54,224,255,.65)}",
    "#demo-cap .demo-text{font-family:'Geist',system-ui,-apple-system,sans-serif;",
      "font-size:17px;font-weight:500;line-height:1.4;color:#fff}"
  ].join("");

  function div(id, cls) {
    var d = document.createElement("div");
    if (id) d.id = id;
    if (cls) d.className = cls;
    return d;
  }

  function start() {
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var bar = div("demo-progress");
    var cap = div("demo-cap");
    var tag = div(null, "demo-tag");
    var text = div(null, "demo-text");
    cap.appendChild(tag);
    cap.appendChild(text);
    document.body.appendChild(bar);
    document.body.appendChild(cap);

    var shownBeat = -1;
    function renderCaption(i) {
      tag.textContent = BEATS[i].tag;
      text.textContent = BEATS[i].text;
      cap.classList.remove("demo-in");
      void cap.offsetWidth;                 // force reflow so the pop replays
      cap.classList.add("demo-in");
    }

    var t0 = performance.now();
    var lastElapsed = 0;
    var ran = ACTIONS.map(function () { return false; });

    function frame(now) {
      var elapsed = (now - t0) % TOTAL;
      if (elapsed < lastElapsed) {           // looped back to the start
        ran = ACTIONS.map(function () { return false; });
      }
      lastElapsed = elapsed;

      bar.style.width = (elapsed / TOTAL * 100).toFixed(2) + "%";

      var bi = Math.min(BEATS.length - 1, Math.floor(elapsed / BEAT_MS));
      if (bi !== shownBeat) { shownBeat = bi; renderCaption(bi); }

      ACTIONS.forEach(function (a, idx) {
        if (!ran[idx] && elapsed >= a.at) {
          ran[idx] = true;
          try { a.fn(); } catch (e) { /* element not ready — skip, retry next loop */ }
        }
      });

      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
