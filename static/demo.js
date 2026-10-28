/* Landing-page instant demo: show/hide the pre-rendered example panels, and make the
 * CTA scroll to + focus the URL field. Purely presentational — works without
 * funnel.js. Without JS the same links are plain #anchors (see .demo-panel:target). */
(function () {
  "use strict";
  var picks = document.querySelectorAll("[data-demo-pick]");
  if (!picks.length) return;

  function panelFor(id) { return document.getElementById("demo-" + id); }

  function open(id) {
    var panel = panelFor(id);
    if (!panel) return;
    Array.prototype.forEach.call(document.querySelectorAll(".demo-panel"), function (p) {
      p.classList.toggle("is-open", p === panel);
    });
    Array.prototype.forEach.call(picks, function (a) {
      a.setAttribute("aria-expanded", a.getAttribute("data-demo-pick") === id ? "true" : "false");
    });
    // make sure the top of the result is visible on a small screen, without jumping if it already is
    var r = panel.getBoundingClientRect();
    if (r.top > window.innerHeight * 0.6 || r.top < 0) panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  Array.prototype.forEach.call(picks, function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();                                   // no hash change, no navigation
      open(a.getAttribute("data-demo-pick"));
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-demo-cta]"), function (a) {
    a.addEventListener("click", function (e) {
      var field = document.getElementById("filter_url");
      if (!field) return;                                   // fall back to the plain #filter_url anchor
      e.preventDefault();
      field.scrollIntoView({ block: "center", behavior: "smooth" });
      field.focus({ preventScroll: true });
    });
  });
})();
