// Tiny helpers shared by the static mockups: theme toggle + nav/sidebar injection.
(function () {
  const saved = (function () { try { return localStorage.getItem('vault-theme'); } catch (e) { return null; } })();
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  window.toggleTheme = function () {
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const next = cur === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('vault-theme', next); } catch (e) {}
  };
  const icons = {
    overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="16" r="3"/></svg>',
    policy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/></svg>',
    db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    scan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M3 9h18M8 21h8"/></svg>',
    report: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    admin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4.4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4.4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/></svg>'
  };
  const nav = [
    ['Operate', [['01-dashboard.html', 'Overview', 'overview'], ['02-playground.html', 'Playground', 'play'], ['#', 'Policies', 'policy']]],
    ['Layers', [['03-data-sources.html', 'Data sources & jobs', 'db'], ['04-ui-scanner.html', 'UI scanner', 'scan']]],
    ['Govern', [['#', 'Reports & audit', 'report'], ['#', 'Admin', 'admin']]]
  ];
  window.renderSidebar = function (active) {
    let html = '<div class="brand"><div class="brand-mark">D</div><div><div class="brand-title">Data Privacy Services</div><div class="brand-sub">Privacy Console</div></div></div><div>';
    nav.forEach(([label, items]) => {
      html += '<div class="nav-label">' + label + '</div>';
      items.forEach(([href, text, ic]) => { html += '<a class="nav-item' + (text === active ? ' active' : '') + '" href="' + href + '">' + icons[ic] + text + '</a>'; });
    });
    html += '</div><div class="sidebar-foot"><span>v0.1 · mockup</span><span class="mono">/api/v1</span></div>';
    document.querySelector('.sidebar').innerHTML = html;
    const b = document.createElement('div'); b.className = 'mock-banner';
    b.innerHTML = 'Static mockup · <a href="index.html">all screens</a> · <a href="#" onclick="toggleTheme();return false;">toggle theme</a>';
    document.body.appendChild(b);
  };
})();
