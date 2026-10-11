// Runs before first paint (same-origin, so the strict CSP stays script-src 'self').
(function () {
  var theme = 'dark';
  try { theme = localStorage.getItem('dps.theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); } catch (e) {}
  document.documentElement.setAttribute('data-theme', theme);
})();
