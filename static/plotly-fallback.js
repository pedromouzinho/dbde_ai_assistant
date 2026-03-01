// Plotly CDN fallback — loaded as external script for CSP compliance
(function () {
  if (typeof window.Plotly === "undefined") {
    var s = document.createElement("script");
    s.src = "/static/vendor/plotly.min.js";
    document.head.appendChild(s);
  }
})();
