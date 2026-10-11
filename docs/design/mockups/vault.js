// Tiny helpers shared by the static mockups: theme toggle, nav/sidebar injection, phone tables.
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
    admin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4.4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4.4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>'
  };
  // Each item opens the home screen of its section. Screens below a section keep that item active:
  // Playground 02 (+ 08 documents) · Data sources & jobs 07 (+ 06 wizard, 03 job detail) ·
  // UI scanner 12 (+ 04 parallel view) · Reports & audit 09 (+ 10 evidence pack).
  const nav = [
    ['Operate', [['01-dashboard.html', 'Overview', 'overview'], ['02-playground.html', 'Playground', 'play'], ['05-policies.html', 'Policies', 'policy']]],
    ['Layers', [['07-jobs.html', 'Data sources & jobs', 'db'], ['12-scanner-setup.html', 'UI scanner', 'scan']]],
    ['Govern', [['09-reports.html', 'Reports & audit', 'report'], ['11-admin.html', 'Admin', 'admin']]]
  ];
  window.renderSidebar = function (active) {
    let html = '<div class="brand"><div class="brand-mark">D</div><div><div class="brand-title">Data Privacy Services</div><div class="brand-sub">Privacy Console</div></div></div><div>';
    nav.forEach(([label, items]) => {
      html += '<div class="nav-label">' + label + '</div>';
      items.forEach(([href, text, ic]) => {
        const on = text === active;
        html += '<a class="nav-item' + (on ? ' active' : '') + '" href="' + href + '"' + (on ? ' aria-current="page"' : '') + '>' + icons[ic] + text + '</a>';
      });
    });
    html += '</div><div class="sidebar-foot"><a href="index.html">v0.1 · all screens</a><span class="mono">/api/v1</span></div>';
    const side = document.querySelector('.sidebar');
    side.id = 'sidebar';
    side.setAttribute('aria-label', 'Main');
    side.innerHTML = html;

    // Below 1100px the sidebar is hidden; a menu button in the topbar opens it as a drawer.
    const shell = document.querySelector('.shell');
    const top = document.querySelector('.topbar');
    if (shell && top && !top.querySelector('.nav-toggle')) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'nav-toggle';
      btn.setAttribute('aria-label', 'Open navigation'); btn.setAttribute('aria-controls', 'sidebar'); btn.setAttribute('aria-expanded', 'false');
      btn.innerHTML = icons.menu;
      top.insertBefore(btn, top.firstChild);
      const scrim = document.createElement('div'); scrim.className = 'nav-scrim';
      shell.appendChild(scrim);
      const setOpen = function (open) {
        shell.classList.toggle('nav-open', open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
        if (open) { const a = side.querySelector('.nav-item.active') || side.querySelector('.nav-item'); if (a) a.focus(); }
      };
      btn.addEventListener('click', function () { setOpen(!shell.classList.contains('nav-open')); });
      scrim.addEventListener('click', function () { setOpen(false); btn.focus(); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && shell.classList.contains('nav-open')) { setOpen(false); btn.focus(); } });
      if (/(^|[?&])nav(&|=|$)/.test(location.search)) setOpen(true); // ?nav opens the drawer for screenshots
    }

    const b = document.createElement('div'); b.className = 'mock-banner';
    b.innerHTML = 'Static mockup · <a href="index.html">all screens</a> · <a href="#" onclick="toggleTheme();return false;">toggle theme</a>';
    (document.querySelector('.main') || document.body).appendChild(b); // in flow at the end of the page, so it never covers content
  };

  // Phone tables: copy each column header into its cells (data-l) so table.stack rows can show labels.
  window.labelCells = function (table) {
    const heads = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) { return th.textContent.trim(); });
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      Array.prototype.forEach.call(tr.children, function (td, i) { if (heads[i] && !td.hasAttribute('colspan')) td.setAttribute('data-l', heads[i]); });
    });
  };
  document.addEventListener('DOMContentLoaded', function () { document.querySelectorAll('table.stack').forEach(window.labelCells); });
})();
