/* AdAgent landing-page funnel tracking — first-party, cookieless, anonymous.
 *
 * What leaves the browser: a random per-tab id (sessionStorage), event name, ui
 * language, a has_gclid boolean (never the gclid value), utm_source/campaign,
 * the referrer HOSTNAME, and for pasted links only {host, coarse label}.
 * The full URL a visitor pastes is never sent from here.
 * Sent with sendBeacon (fetch keepalive as fallback) to window.AA_FUNNEL.endpoint.
 * Every line is wrapped so tracking can never break the page or the form.
 */
(function () {
  "use strict";
  var cfg = window.AA_FUNNEL;
  if (!cfg || !cfg.endpoint) return;

  var mem = {};                                    // fallback when sessionStorage is blocked
  function get(k) { try { return sessionStorage.getItem(k); } catch (e) { return mem[k] || null; } }
  function set(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { mem[k] = v; } }

  function randomId() {
    var a = [], i;
    try {
      var buf = new Uint8Array(16);
      crypto.getRandomValues(buf);
      for (i = 0; i < 16; i++) a.push(("0" + buf[i].toString(16)).slice(-2));
    } catch (e) {
      for (i = 0; i < 16; i++) a.push(("0" + Math.floor(Math.random() * 256).toString(16)).slice(-2));
    }
    return a.join("");
  }

  var sid = get("aa_sid");
  if (!sid || !/^[a-f0-9]{32}$/.test(sid)) { sid = randomId(); set("aa_sid", sid); }

  var params = new URLSearchParams(location.search);
  if (params.has("gclid") || params.has("wbraid") || params.has("gbraid")) set("aa_gc", "1");
  else if (get("aa_gc") === null) set("aa_gc", "0");
  ["utm_source", "utm_campaign"].forEach(function (k) {
    var v = params.get(k);
    if (v) set("aa_" + k, v.slice(0, 64));
  });

  // Same rules as service/funnel.py:url_label — keep in sync.
  var HOSTS = ["olx.pl", "otomoto.pl", "otodom.pl"];
  var SEGS = ["motoryzacja", "nieruchomosci", "osobowe", "dostawcze", "motocykle", "wyniki",
              "sprzedaz", "wynajem", "mieszkanie", "dom", "oferta", "d"];
  function labelFor(value) {
    var u;
    try { u = new URL((value || "").trim()); } catch (e) { return { host: "other", label: "other:other" }; }
    var h = u.hostname.toLowerCase().replace(/^www\./, ""), host = null, i;
    for (i = 0; i < HOSTS.length; i++) if (h === HOSTS[i] || h.slice(-(HOSTS[i].length + 1)) === "." + HOSTS[i]) host = HOSTS[i];
    if (!host) return { host: "other", label: "other:other" };
    var segs = u.pathname.toLowerCase().split("/").filter(Boolean);
    var q = u.search.toLowerCase();
    var seg = "other";
    for (i = 0; i < Math.min(3, segs.length); i++) if (SEGS.indexOf(segs[i]) > -1) { seg = segs[i]; break; }
    var isAd = host === "olx.pl" ? (segs[0] === "d" && segs[1] === "oferta")
             : host === "otomoto.pl" ? segs.indexOf("oferta") > -1
             : segs.slice(0, 2).indexOf("oferta") > -1;
    var kind;
    if (isAd) kind = "ad";
    else if (q.indexOf("search%5b") > -1 || q.indexOf("search[") > -1 ||
             segs.some(function (s) { return s.indexOf("q-") === 0; }) ||
             (host === "otodom.pl" && (segs.indexOf("wyniki") > -1 || q))) kind = "filter";
    else if (segs.length) kind = "category";
    else kind = "other";
    return { host: host, label: seg + ":" + kind };
  }

  function send(event, extra) {
    try {
      var p = {
        event: event, sid: sid, gclid: get("aa_gc") === "1", lang: cfg.lang,
        utm_source: get("aa_utm_source") || undefined, utm_campaign: get("aa_utm_campaign") || undefined
      };
      for (var k in (extra || {})) p[k] = extra[k];
      var body = JSON.stringify(p);
      // text/plain keeps it a "simple" request (no CORS preflight); the server ignores the type.
      if (navigator.sendBeacon && navigator.sendBeacon(cfg.endpoint, new Blob([body], { type: "text/plain" }))) return;
      fetch(cfg.endpoint, { method: "POST", body: body, keepalive: true, credentials: "omit",
                            headers: { "Content-Type": "text/plain" } });
    } catch (e) { /* never break the page */ }
  }

  function once(key, fn) { if (get(key) === "1") return; set(key, "1"); fn(); }
  function safe(fn) { return function (e) { try { fn(e); } catch (err) {} }; }

  // ── landing (once per tab session, so reloads don't inflate it) ─────────────
  safe(function () {
    once("aa_landed", function () {
      var ref;
      try { ref = document.referrer ? new URL(document.referrer).hostname : undefined; } catch (e) {}
      if (ref === location.hostname) ref = undefined;
      send("landing", { ref: ref });
    });
  })();

  // ── hidden sid field so the server can attribute /analyze and invalid_url ───
  safe(function () {
    var f = document.querySelector('form[action="/analyze"]');
    if (!f) return;
    var h = f.querySelector('input[name="sid"]');
    if (!h) { h = document.createElement("input"); h.type = "hidden"; h.name = "sid"; f.appendChild(h); }
    h.value = sid;
  })();

  // ── demo block seen (impression, once per tab session) ──────────────────────
  safe(function () {
    var demo = document.getElementById("demo");
    if (!demo || !("IntersectionObserver" in window)) return;
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) {
        io.disconnect();
        once("aa_demo_seen", function () { send("demo_open"); });
      }
    }, { threshold: 0.4 });
    io.observe(demo);
  })();

  // ── clicks: demo picks, demo CTA, sample report ─────────────────────────────
  document.addEventListener("click", safe(function (e) {
    var el = e.target && e.target.closest ? e.target.closest("[data-demo-pick],[data-demo-cta],[data-funnel]") : null;
    if (!el) return;
    if (el.hasAttribute("data-demo-pick")) send("demo_pick", { detail: el.getAttribute("data-demo-pick") });
    else if (el.hasAttribute("data-demo-cta")) send("demo_cta_click", { detail: el.getAttribute("data-demo-cta") });
    else if (el.getAttribute("data-funnel") === "sample") send("sample_report_click");
  }), true);

  // ── URL field ───────────────────────────────────────────────────────────────
  var input = document.getElementById("filter_url");
  if (input) {
    var pasted = false, lastLen = 0;
    function reportPaste() {
      var l = labelFor(input.value);
      send("url_paste", { host: l.host, label: l.label });
    }
    input.addEventListener("focus", safe(function () { once("aa_focus_" + location.pathname, function () { send("url_field_focus"); }); }));
    input.addEventListener("paste", safe(function () {
      pasted = true; setTimeout(safe(reportPaste), 0);            // value is updated after the event
    }));
    // Mobile keyboards / autofill sometimes insert a whole link without a paste event.
    input.addEventListener("input", safe(function (e) {
      var len = input.value.length;
      if (!pasted && len - lastLen > 12) { pasted = true; reportPaste(); }
      if (!len) pasted = false;
      lastLen = len;
    }));
    // Browser-side validation (type=url / required) blocks submit silently: no request is made.
    input.addEventListener("invalid", safe(function () {
      var v = input.validity, why = v.valueMissing ? "client_empty" : v.typeMismatch ? "client_not_url" : "client_other";
      send("invalid_url", { detail: why });
    }));
    var form = input.form;
    if (form) form.addEventListener("submit", safe(function () {
      var l = labelFor(input.value);
      send("analyze_submit", { host: l.host, label: l.label });
    }));
  }
})();
