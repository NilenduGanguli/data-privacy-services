// Page specifications for the demo onboarding site. render(path, url) returns HTML or null.
// Every value comes from data.json (synthetic); the tests treat them as customer data.
import { readFileSync } from 'node:fs';

const DATA = JSON.parse(readFileSync(new URL('./data.json', import.meta.url), 'utf8'));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function layout(title, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} — Demo Bank onboarding</title><link rel="stylesheet" href="/static/app.css"></head>
<body><header class="top"><a href="/" class="brand">Demo Bank</a><nav><a href="/apply">Apply</a><a href="/apply/us">Apply (US)</a><a href="/profile">Profile</a><a href="/app">Nominees</a><a href="/help">Help</a><a href="/login">Sign in</a><a href="/logout">Log out</a></nav></header>
<main><h1>${esc(title)}</h1>${body}</main><script src="/static/forms.js"></script></body></html>`;
}

const ATTRS = ['name', 'id', 'type', 'autocomplete', 'pattern', 'maxlength', 'placeholder', 'inputmode', 'title', 'aria-label', 'aria-labelledby', 'value'];
const attrs = (f) => ATTRS.filter((k) => f[k] !== undefined).map((k) => ` ${k}="${esc(f[k])}"`).join('') + (f.required ? ' required' : '');

function control(f) {
  const described = f.id ? ` aria-describedby="${esc(f.id)}-msg"` : '';
  if (f.tag === 'select') {
    const opts = f.options.map((o) => `<option value="${esc(o)}">${esc(o || 'Choose…')}</option>`).join('');
    return `<select${attrs(f)}${described}${f.autosubmit ? ' data-autosubmit' : ''}>${opts}</select>`;
  }
  if (f.tag === 'textarea') return `<textarea${attrs({ ...f, value: undefined })}${described} rows="2">${esc(f.value ?? '')}</textarea>`;
  if (f.tag === 'postal-lookup') return '<postal-lookup></postal-lookup>';
  return `<input${attrs({ type: 'text', ...f })}${described}>`;
}

function field(f) {
  const msg = f.id ? `<span class="msg" id="${esc(f.id)}-msg" aria-live="polite"></span>` : '';
  const cls = `field${f.full ? ' full' : ''}`;
  switch (f.labelStyle ?? 'for') {
    case 'for': return `<div class="${cls}"><label for="${esc(f.id)}">${esc(f.label)}</label>${control(f)}${msg}</div>`;
    case 'wrap': return `<div class="${cls}"><label>${esc(f.label)} ${control(f)}</label>${msg}</div>`;
    case 'labelledby': return `<div class="${cls}"><span class="lbl" id="${esc(f['aria-labelledby'])}">${esc(f.label)}</span>${control(f)}${msg}</div>`;
    case 'near': return `<div class="${cls}"><div class="lbl">${esc(f.label)}</div>${control(f)}${msg}</div>`;
    default: return `<div class="${cls}">${control(f)}${msg}</div>`; // aria-label / placeholder / title / none
  }
}

function form(spec) {
  const data = [spec.autosave ? ' data-autosave="/api/autosave"' : '', spec.beacon ? ' data-beacon' : ''].join('');
  return `<form id="${esc(spec.id)}" method="${spec.method ?? 'post'}" action="${esc(spec.action)}"${data} class="grid">
${spec.fields.map(field).join('\n')}
<div class="actions"><button type="submit">${esc(spec.submitLabel ?? 'Continue →')}</button></div></form>`;
}

const links = (ls) => `<ul class="links">${ls.map(([href, text, extra = '']) => `<li><a href="${esc(href)}"${extra}>${esc(text)}</a></li>`).join('')}</ul>`;
const REVIEW_LINK = `/apply/review?email=${encodeURIComponent(DATA.email)}&ref=${encodeURIComponent(DATA.application_ref)}`;
const SEL = (name, id, label, options, extra = {}) => ({ tag: 'select', name, id, label, options: ['', ...options], ...extra });

const PAGES = {
  '/': { title: 'Welcome', body: () => `<p>Open an account in minutes.</p>${links([
    ['/apply', 'Apply for an account'], ['/apply/us', 'Apply from the United States'], ['/profile', 'My profile'],
    ['/app', 'Nominee centre'], ['/help', 'Help'], ['/login', 'Sign in'], ['/logout', 'Log out'], ['/admin', 'Administration'],
    ['https://cdn.bank-assets.invalid/terms', 'Terms (external)'], ['mailto:help@example.org', 'Mail us'],
    ['/files/brochure.pdf', 'Brochure', ' download']])}<img src="http://tracker.invalid/pixel.gif" alt="" width="1" height="1">` },
  '/apply': { title: 'Apply for an account', form: { id: 'start', action: '/apply/start', fields: [
    SEL('product', 'product', 'Product', ['Savings', 'Salary', 'Current']),
    SEL('country', 'country', 'Country of residence', ['India', 'United States']),
    { label: 'Full name', name: 'fullName', id: 'fullName', autocomplete: 'name', required: true },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', required: true }] },
    after: () => `<p>Application steps:</p>${links([['/apply/identity', 'Identity details'], ['/apply/address', 'Address'],
      ['/apply/employment', 'Employment'], ['/apply/card', 'Debit card'], ['/apply/documents', 'Documents']])}` },
  '/apply/identity': { title: 'Identity details', form: { id: 'identity', action: '/apply/identity/submit', autosave: true, beacon: true, fields: [
    { label: 'Full name', name: 'fullName', id: 'fullName', autocomplete: 'name', required: true },
    { label: 'Date of birth', name: 'dob', id: 'dob', autocomplete: 'bday', placeholder: 'DD/MM/YYYY', pattern: '\\d{2}/\\d{2}/\\d{4}', required: true },
    { label: 'Aadhaar number', name: 'aadhaar', id: 'aadhaar', inputmode: 'numeric', placeholder: 'XXXX XXXX XXXX', pattern: '\\d{4} \\d{4} \\d{4}', required: true },
    { label: 'PAN', name: 'pan', id: 'pan', maxlength: 10, pattern: '[A-Z]{5}[0-9]{4}[A-Z]', required: true },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', required: true },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Residential address', name: 'address', id: 'address', tag: 'textarea', autocomplete: 'street-address', full: true },
    SEL('gender', 'gender', 'Gender', ['Female', 'Male', 'Other'], { autocomplete: 'sex' }),
    SEL('nationality', 'nationality', 'Nationality', ['Indian', 'American', 'British', 'Canadian', 'Mexican'])] },
    after: () => links([['/apply/address', 'Continue to address'], [REVIEW_LINK, 'Review application']]) },
  '/apply/address': { title: 'Address', form: { id: 'address', action: '/apply/address/submit', autosave: true, fields: [
    { label: 'Address line 1', name: 'line1', id: 'line1', autocomplete: 'address-line1', required: true },
    { label: 'Address line 2', name: 'line2', autocomplete: 'address-line2', labelStyle: 'wrap' },
    { label: 'City', name: 'city', 'aria-labelledby': 'city-lbl', autocomplete: 'address-level2', labelStyle: 'labelledby' },
    SEL('state', undefined, 'State', ['Karnataka', 'Maharashtra', 'Tamil Nadu', 'Delhi'], { 'aria-label': 'State', autocomplete: 'address-level1', labelStyle: 'none' }),
    { tag: 'postal-lookup', labelStyle: 'none' },
    { name: 'landmark', placeholder: 'Landmark (optional)', labelStyle: 'none' },
    SEL('residence', undefined, 'Country of residence', ['India', 'United States', 'United Kingdom'], { labelStyle: 'near' })] },
    after: () => links([['/apply/employment', 'Continue to employment']]) },
  '/apply/employment': { title: 'Employment', form: { id: 'employment', action: '/apply/employment/submit', fields: [
    { label: 'Employer', name: 'employer', id: 'employer', autocomplete: 'organization' },
    { label: 'Occupation', name: 'occupation', id: 'occupation' },
    { label: 'Annual income (INR)', name: 'income', id: 'income', type: 'number' },
    { label: 'IFSC', name: 'ifsc', id: 'ifsc', maxlength: 11, pattern: '[A-Z]{4}0[A-Z0-9]{6}' },
    { label: 'Account number', name: 'account', id: 'account', inputmode: 'numeric', pattern: '\\d{9,18}' },
    { label: 'Employee ID', name: 'employeeId', id: 'employeeId', pattern: 'EMP-\\d{6}' }] },
    after: () => links([['/apply/card', 'Continue to card']]) },
  '/apply/card': { title: 'Debit card', form: { id: 'card', action: '/apply/card/submit', fields: [
    { label: 'Name to emboss', name: 'emboss', id: 'emboss', autocomplete: 'cc-name' },
    SEL('delivery', 'delivery', 'Card delivery', ['Home address', 'Branch pickup'], { autosubmit: true })] },
    after: () => `<iframe name="card-widget" title="Card details" src="/frames/card-widget" style="width:100%;height:330px;border:0"></iframe>${links([['/apply/documents', 'Continue to documents']])}` },
  '/apply/documents': { title: 'Documents', form: { id: 'documents', action: '/apply/documents/submit', fields: [
    { label: 'Proof of address (PDF or JPEG)', name: 'addressProof', id: 'addressProof', type: 'file' }] } },
  '/apply/us': { title: 'Apply from the United States', form: { id: 'us', action: '/apply/us/submit', autosave: true, fields: [
    { label: 'First name', name: 'firstName', id: 'firstName', autocomplete: 'given-name', required: true },
    { label: 'Last name', name: 'lastName', id: 'lastName', autocomplete: 'family-name', required: true },
    { label: 'Social Security number', name: 'ssn', id: 'ssn', pattern: '\\d{3}-\\d{2}-\\d{4}', placeholder: '000-00-0000', required: true },
    { label: 'Date of birth', name: 'dob', id: 'dob', type: 'date', autocomplete: 'bday' },
    { label: 'Phone', name: 'phone', id: 'phone', type: 'tel', autocomplete: 'tel' },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email' },
    { label: 'Street address', name: 'street', id: 'street', autocomplete: 'address-line1' },
    { label: 'City', name: 'city', id: 'city', autocomplete: 'address-level2' },
    SEL('state', 'state', 'State', ['CA', 'NY', 'TX', 'WA'], { autocomplete: 'address-level1' }),
    { label: 'ZIP code', name: 'zip', id: 'zip', autocomplete: 'postal-code', pattern: '\\d{5}' },
    { label: 'Driver license number', name: 'dl', id: 'dl' }] } },
  '/apply/review': { title: 'Review your application', body: () => `<dl class="review">
<dt>Full name</dt><dd>${esc(DATA.full_name)}</dd><dt>Email</dt><dd>${esc(DATA.email)}</dd><dt>Mobile</dt><dd>${esc(DATA.mobile)}</dd>
<dt>Aadhaar</dt><dd>XXXX XXXX ${esc(DATA.aadhaar.slice(-4))}</dd><dt>PAN number</dt><dd>${esc(DATA.pan)}</dd>
<dt>Application reference</dt><dd>${esc(DATA.application_ref)}</dd></dl>` },
  '/login': { title: 'Sign in', form: { id: 'login', action: '/login', submitLabel: 'Sign in', fields: [
    { label: 'Username', name: 'username', id: 'username', autocomplete: 'username', required: true },
    { label: 'Password', name: 'password', id: 'password', type: 'password', required: true },
    { label: 'One-time code', name: 'otp', id: 'otp', autocomplete: 'one-time-code', inputmode: 'numeric', maxlength: 6, pattern: '\\d{6}' }] } },
  // Account number and customer id rows: page redaction must cover them (Stage A lesson 3).
  '/profile': { title: 'My profile', body: () => `<table class="profile"><tbody>
${[['Full name', DATA.full_name], ['Date of birth', DATA.dob], ['Customer ID', DATA.customer_id], ['Aadhaar', DATA.aadhaar],
   ['PAN number', DATA.pan], ['Account number', DATA.account_number], ['Card number', DATA.card_number],
   ['Email', DATA.email], ['Mobile', DATA.mobile], ['Address', DATA.address]].map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('\n')}
</tbody></table>${links([['/profile/contact', 'Edit contact details']])}` },
  '/profile/contact': { title: 'Contact details', form: { id: 'contact', action: '/profile/contact/save', autosave: true, fields: [
    { label: 'Contact detail', name: 'contact1', id: 'contact1', value: DATA.email },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', value: DATA.mobile },
    { label: 'Address', name: 'address', id: 'address', tag: 'textarea', value: DATA.address, full: true }] } },
  '/help': { title: 'Help', body: () => links([['/help/contact-us', 'Contact us'], ['/help/track', 'Track an application'],
    ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => [`/help/faq/${n}`, `FAQ ${n}`])]) },
  '/help/contact-us': { title: 'Contact us', form: { id: 'contact-us', action: '/help/contact-us', submitLabel: 'Send', fields: [
    { label: 'Your name', name: 'name', id: 'name', autocomplete: 'name' },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email' },
    { label: 'Phone', name: 'phone', id: 'phone', type: 'tel', autocomplete: 'tel' },
    { label: 'Message', name: 'message', id: 'message', tag: 'textarea', full: true }] } },
  '/help/track': { title: 'Track an application', form: { id: 'track', method: 'get', action: '/help/track/result', submitLabel: 'Track', fields: [
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Application reference', name: 'ref', id: 'ref', pattern: 'APP-\\d{4}-\\d{4}' }] } },
  '/thanks': { title: 'Thank you', body: () => '<p>We received your details.</p>' },
  // Not linked from any page: the crawler's dialog and popup handling (tests only).
  '/fixtures/dialogs': { title: 'Dialogs', body: () => "<p>Pop-ups and alerts.</p><script>alert('Welcome to the demo'); window.open('/help', '_blank');</script>" },
  '/fixtures/labels': { title: 'Label fixture', body: () => `<form id="fixture" action="/fixture" method="post">
<label for="a1">Customer email</label><input id="a1" name="a1">
<label>Mobile number <input name="a2"></label>
<span id="l3">Passport number</span><input name="a3" aria-labelledby="l3">
<input name="a4" aria-label="Date of birth">
<input name="a5" placeholder="PIN code">
<div><span>Aadhaar</span><input name="a6"></div>
<input name="a7" title="Employee ID">
<input name="a8">
<label for="a9">Occupation</label><select id="a9" name="a9"><option value="">Choose</option><option>Salaried</option><option>Self-employed</option><option>Student</option></select>
<input type="hidden" name="csrf" value="tok-123">
<button type="submit">Send</button><input type="submit" value="Go"><input type="button" value="Help"><input type="reset">
</form>` },
};

function faq(n) {
  const next = n < 8 ? links([[`/help/faq/${n + 1}`, 'Next question']]) : '';
  return layout(`FAQ ${n}`, `<p>Answer number ${n}. No forms here.</p>${next}`);
}

export function render(path, url) { // eslint-disable-line no-unused-vars
  const m = /^\/help\/faq\/(\d+)$/.exec(path);
  if (m) return Number(m[1]) >= 1 && Number(m[1]) <= 8 ? faq(Number(m[1])) : null;
  if (path === '/app' || path.startsWith('/app/')) return layout('Nominee centre', `<div id="app"><nav class="spa"><a href="/app/nominee" data-spa>Nominee</a> · <span role="link" tabindex="0" data-route="/app/preferences" class="linkish">Preferences</span></nav><section id="view"></section></div><script src="/static/spa.js"></script>`);
  if (path === '/frames/card-widget') return `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/app.css"></head><body class="widget">
${form({ id: 'card-widget', action: '/frames/card-widget', autosave: true, submitLabel: 'Save card', fields: [
    { label: 'Name on card', name: 'ccName', id: 'ccName', autocomplete: 'cc-name' },
    { label: 'Card number', name: 'ccNumber', id: 'ccNumber', autocomplete: 'cc-number', inputmode: 'numeric', maxlength: 19 },
    { label: 'Expiry', name: 'ccExp', id: 'ccExp', autocomplete: 'cc-exp', placeholder: 'MM/YY', pattern: '(0[1-9]|1[0-2])/\\d{2}' },
    { label: 'CVV', name: 'ccCsc', id: 'ccCsc', autocomplete: 'cc-csc', maxlength: 3, inputmode: 'numeric' }] })}
<script src="/static/forms.js"></script></body></html>`;
  if (!Object.hasOwn(PAGES, path)) return null;
  const page = PAGES[path];
  const body = (page.body ? page.body() : '') + (page.form ? form(page.form) : '') + (page.after ? page.after() : '');
  const extra = path === '/apply/address' ? '<script src="/static/postal-lookup.js"></script>' : '';
  return layout(page.title, body + extra);
}
