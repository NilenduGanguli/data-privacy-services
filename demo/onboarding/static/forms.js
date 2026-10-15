// Demo-site form behaviours: autosave POST, a telemetry beacon, a draft flush when the page goes
// away, client-side validation messages, and an auto-submitting select. A real onboarding app does
// all of these; DPS must defeat each.
(() => {
  for (const form of document.querySelectorAll('form')) {
    if (form.dataset.autosave) {
      let timer = null;
      form.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          fetch(form.dataset.autosave, { method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify(Object.fromEntries(new FormData(form))) }).catch(() => {});
        }, 150);
      });
    }
    if (form.dataset.autosave) {
      // Draft flush on the way out (pagehide / hidden): a beacon and a keepalive fetch, which the
      // browser still sends after the page is gone. Closing a filled page must not let it out.
      const flush = () => {
        const data = Object.fromEntries(new FormData(form));
        if (!Object.values(data).some((v) => typeof v === 'string' && v !== '')) return;
        const body = JSON.stringify(data);
        navigator.sendBeacon('/api/draft', body);
        fetch('/api/draft', { method: 'POST', body, keepalive: true, headers: { 'content-type': 'application/json' } }).catch(() => {});
      };
      addEventListener('pagehide', flush);
      document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(); });
    }
    if (form.dataset.beacon !== undefined) {
      form.addEventListener('focusin', () => navigator.sendBeacon('/api/telemetry/form-start', JSON.stringify({ form: form.id })), { once: true });
    }
    for (const el of form.querySelectorAll('input, select, textarea')) {
      el.addEventListener('blur', () => {
        const msg = el.id ? document.getElementById(`${el.id}-msg`) : null;
        if (!msg) return;
        if (el.checkValidity()) { msg.textContent = ''; el.removeAttribute('aria-invalid'); }
        else { msg.textContent = 'Use the format shown'; el.setAttribute('aria-invalid', 'true'); }
      });
    }
    for (const sel of form.querySelectorAll('select[data-autosubmit]')) {
      sel.addEventListener('change', () => form.requestSubmit());
    }
  }
})();
