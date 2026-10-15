// Client-side router for /app (History API). /app/preferences is reachable only by clicking a
// role="link" element that has no href — the scanner must discover it by clicking.
(() => {
  const view = document.getElementById('view');
  const field = (label, attrs) => `<div class="field"><label for="${attrs.id}">${label}</label><input ${Object.entries(attrs).map(([k, v]) => `${k}="${v}"`).join(' ')}></div>`;
  const ROUTES = {
    '/app': () => '<p>Manage nominees and preferences.</p>',
    '/app/nominee': () => `<form id="nominee" method="post" action="/app/nominee" class="grid">
      ${field('Nominee full name', { id: 'nomName', name: 'nomName' })}
      <div class="field"><label for="nomRel">Relationship</label><select id="nomRel" name="nomRel"><option value="">Choose…</option><option>Spouse</option><option>Parent</option><option>Child</option></select></div>
      ${field('Nominee date of birth', { id: 'nomDob', name: 'nomDob', type: 'date' })}
      ${field('Nominee Aadhaar', { id: 'nomAadhaar', name: 'nomAadhaar', pattern: '\\d{4} \\d{4} \\d{4}', placeholder: 'XXXX XXXX XXXX' })}
      <div class="actions"><button type="submit">Save nominee</button></div></form>`,
    '/app/preferences': () => `<form id="prefs" method="post" action="/app/preferences" class="grid">
      <div class="field"><label for="stmt">Statement delivery</label><select id="stmt" name="stmt"><option value="">Choose…</option><option>Email</option><option>Post</option></select></div>
      <div class="field"><label><input type="checkbox" name="marketing"> Send me offers</label></div>
      <div class="actions"><button type="submit">Save</button></div></form>`,
  };
  const render = () => { view.innerHTML = (ROUTES[location.pathname] ?? ROUTES['/app'])(); };
  const go = (path) => { history.pushState({}, '', path); render(); };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-spa]');
    if (a) { e.preventDefault(); go(a.getAttribute('href')); return; }
    const r = e.target.closest('[role="link"][data-route]');
    if (r) { e.preventDefault(); go(r.dataset.route); }
  });
  window.addEventListener('popstate', render);
  render();
})();
