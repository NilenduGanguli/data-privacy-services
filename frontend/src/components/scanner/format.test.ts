// Value-free display helpers of the scanner pages (mockups 12 and 04).
import { describe, expect, it } from 'vitest';
import { event, field, page } from '../../lib/scanFixtures';
import {
  autocompleteText, entityChip, entityList, eventNote, eventResultTone, eventVerb, evidenceLabel, fillMethod, flagSentence,
  flagTone, fmtElapsed, hostOf, inputText, pathOf, skipLabel, statusIcon,
} from './format';

describe('paths and hosts', () => {
  it('shows the path and the redacted query, placeholders as •••', () => {
    expect(pathOf('http://demo-site:8410/apply/review?email=<EMAIL>')).toBe('/apply/review?email=•••');
    expect(pathOf('http://demo-site:8410/help/track?ref=<v>&email=%3CEMAIL%3E')).toBe('/help/track?ref=•••&email=•••');
    expect(pathOf('http://demo-site:8410')).toBe('/');
    expect(pathOf('https://h.example/accounts/{id}/<ACCOUNT_NUMBER>')).toBe('/accounts/{id}/•••');
    expect(pathOf(null)).toBe('—');
  });
  it('keeps the origin of a URL on another origin than the scan', () => {
    expect(pathOf('https://cdn.bank-assets.invalid', 'http://demo-site:8410')).toBe('https://cdn.bank-assets.invalid');
    expect(pathOf('http://demo-site:8410/apply', 'http://demo-site:8410')).toBe('/apply');
  });
  it('names the host with its port', () => {
    expect(hostOf('http://demo-site:8410/apply')).toBe('demo-site:8410');
    expect(hostOf('https://onboarding-uat.bank.internal/apply?x=1')).toBe('onboarding-uat.bank.internal');
    expect(hostOf('not a url')).toBe('');
  });
});

describe('page status and skip reasons', () => {
  it('maps each page status to the crawl-map dot', () => {
    expect(statusIcon(page({ status: 'done' }))).toMatchObject({ cls: 'ok', glyph: '✓' });
    expect(statusIcon(page({ status: 'visiting' }))).toMatchObject({ cls: 'run' });
    expect(statusIcon(page({ status: 'queued' }))).toMatchObject({ cls: 'q', glyph: '' });
    expect(statusIcon(page({ status: 'skipped' }))).toMatchObject({ cls: 'skip', glyph: '–' });
    expect(statusIcon(page({ status: 'failed' }))).toMatchObject({ cls: 'skip', glyph: '–' });
    expect(statusIcon(page({ status: 'redirected' }))).toMatchObject({ cls: 'ok', glyph: '↪' });
  });
  it('words every skip reason', () => {
    expect(skipLabel('deny')).toBe('deny list');
    expect(skipLabel('other_origin')).toBe('other origin');
    expect(skipLabel('unregistered_host')).toBe('unregistered host');
    expect(skipLabel('not_allowed')).toBe('outside allow list');
    expect(skipLabel('depth')).toBe('depth limit');
    expect(skipLabel('template')).toBe('template limit, links not followed');
    expect(skipLabel('budget')).toBe('page budget');
    expect(skipLabel('some_new_reason')).toBe('some new reason');
    expect(skipLabel(null)).toBe('skipped');
  });
});

describe('risk flags and entities', () => {
  const base = { severity: 'high' as const, page_no: 2, field_nos: [], entity_types: [], query_keys: [], where: null };
  it('writes the sentence of each flag', () => {
    expect(flagSentence({ ...base, flag: 'autocomplete_enabled_sensitive', entity_types: ['AADHAAR', 'PAN_IN'] })).toBe('autocomplete on for Aadhaar and PAN');
    expect(flagSentence({ ...base, flag: 'password_without_autocomplete_token' })).toBe('password field without new-password/current-password');
    expect(flagSentence({ ...base, flag: 'pii_in_query_string', query_keys: ['email'] })).toBe('PII in query string: ?email=');
    expect(flagSentence({ ...base, flag: 'sensitive_form_uses_get', entity_types: ['EMAIL'] })).toBe('form sends e-mail in the URL (method GET)');
    expect(flagSentence({ ...base, flag: 'pii_rendered_unmasked', entity_types: ['PAN_IN', 'CARD_PAN', 'EMAIL'], where: 'table' }))
      .toBe('PAN, card number and e-mail rendered unmasked in a table');
    expect(flagSentence({ ...base, flag: 'pii_rendered_unmasked', entity_types: ['PERSON_NAME'], where: 'text' })).toBe('name rendered unmasked');
  });
  it('colours flags by severity and entities by sensitivity', () => {
    expect(flagTone({ severity: 'high' })).toBe('rose');
    expect(flagTone({ severity: 'medium' })).toBe('amber');
    expect(entityChip('CRITICAL')).toBe('rose');
    expect(entityChip('HIGH')).toBe('amber');
    expect(entityChip('MEDIUM')).toBe('amber');
    expect(entityChip('LOW')).toBe('slate');
    expect(entityChip(null)).toBe('slate');
  });
  it('lists entities in words', () => {
    expect(entityList([])).toBe('personal data');
    expect(entityList(['SSN'])).toBe('SSN');
    expect(entityList(['STREET_ADDRESS', 'POSTAL_CODE'])).toBe('street address and postal code');
  });
});

describe('fields', () => {
  it('describes the input, autocomplete, fill method and evidence', () => {
    expect(inputText(field({ tag: 'input', input_type: 'text', name_attr: 'fullName', pattern: null, maxlength: null }))).toBe('text · name=fullName');
    expect(inputText(field({ tag: 'input', input_type: 'text', name_attr: null, pattern: '\\d{4} \\d{4} \\d{4}', maxlength: null }))).toBe('text · pattern \\d{4} \\d{4} \\d{4}');
    expect(inputText(field({ tag: 'input', input_type: 'text', name_attr: null, pattern: null, maxlength: 10 }))).toBe('text · maxlength=10');
    expect(inputText(field({ tag: 'select', input_type: null, name_attr: 'gender', pattern: null, maxlength: null }))).toBe('select · name=gender');
    expect(autocompleteText(field({ autocomplete: null }))).toBe('—');
    expect(autocompleteText(field({ autocomplete: 'bday' }))).toBe('bday');
    // an absent attribute means the browser default (on) when the field carries an autocomplete risk flag
    const a1 = { code: 'A1', path: '/apply/identity', flag: 'autocomplete_enabled_sensitive' as const, severity: 'high' as const, page_no: 2,
      field_nos: [3], entity_types: ['AADHAAR'], query_keys: [], where: 'form' as const };
    expect(autocompleteText(field({ autocomplete: null, risk_codes: ['A1'] }), [a1])).toBe('on');
    expect(autocompleteText(field({ autocomplete: 'off', risk_codes: [] }), [a1])).toBe('off');
    expect(fillMethod(field({ persona_attr: 'aadhaar', obfuscate_method: 'fpe_digits' }))).toBe('persona.aadhaar / fpe_digits');
    expect(fillMethod(field({ persona_attr: null, obfuscate_method: null }))).toBe('— / keep');
    expect(evidenceLabel(['value', 'label', 'token'])).toBe('token + label + value');
    expect(evidenceLabel(['maxlength', 'pattern', 'type', 'name'])).toBe('name + type + pattern + maxlength');
    expect(evidenceLabel([])).toBe('—');
  });
});

describe('navigation log', () => {
  const pages = new Map([[3, page({ page_no: 3, url_redacted: 'http://demo-site:8410/apply/address', form_count: 1, field_count: 7 })]]);
  it('writes the note of each event', () => {
    expect(eventNote(event({ kind: 'request', result: 'blocked', method: 'POST', page_no: 3, note_code: 'method' }), pages))
      .toBe('from filled page /apply/address · interception');
    expect(eventNote(event({ kind: 'request', result: 'blocked', method: 'GET', page_no: 3, note_code: 'filled_navigation' }), pages))
      .toBe('navigation from filled page /apply/address · interception');
    expect(eventNote(event({ kind: 'request', result: 'blocked', method: 'GET', page_no: null, note_code: 'host' }), pages)).toBe('host outside the scan · interception');
    expect(eventNote(event({ kind: 'skip', result: 'skipped', note_code: 'deny' }), pages)).toBe('deny list');
    expect(eventNote(event({ kind: 'skip', result: 'skipped', note_code: 'other_origin' }), pages)).toBe('other origin');
    expect(eventNote(event({ kind: 'fill', result: 'captured', note_code: 'synthesize', counts: { fields: 34, rejected: 2, persona: 7 } }), pages))
      .toBe('persona 7 · 34 fields · 2 validation messages recorded');
    expect(eventNote(event({ kind: 'fill', result: 'captured', note_code: 'obfuscate', counts: { fields: 9, rejected: 0, persona: 7 } }), pages))
      .toBe('tokens · 9 fields · all values accepted');
    expect(eventNote(event({ kind: 'fill', result: 'captured', note_code: 'synthesize', counts: { fields: 1, rejected: 1, persona: 1 } }), pages))
      .toBe('persona 1 · 1 field · 1 validation message recorded');
    expect(eventNote(event({ kind: 'capture', result: 'captured', note_code: 'obfuscate' }), pages)).toBe('obfuscated capture');
    expect(eventNote(event({ kind: 'capture', result: 'withheld', note_code: 'residual' }), pages)).toBe('withheld — a value could not be masked');
    expect(eventNote(event({ kind: 'spa', result: 'queued', note_code: 'spa_click' }), pages)).toBe('SPA route found by clicking');
    expect(eventNote(event({ kind: 'spa', result: 'skipped', note_code: 'deny' }), pages)).toBe('not clicked · deny list');
    expect(eventNote(event({ kind: 'spa', result: 'reached', note_code: 'deny' }), pages))
      .toBe("the page's own click handler ran a refused route (deny list) · not crawled");
    expect(eventResultTone('reached')).toBe('rose');
    expect(eventNote(event({ kind: 'navigate', result: '200', method: 'GET', page_no: 3, note_code: 'link' }), pages)).toBe('1 form · 7 fields');
    expect(eventNote(event({ kind: 'navigate', result: 'failed', method: 'GET', page_no: 9, note_code: 'timeout' }), pages)).toBe('failed · timeout');
    expect(eventNote(event({ kind: 'dialog', result: 'dismissed', note_code: 'confirm' }), pages)).toBe('confirm dialog dismissed');
  });
  it('names the event and colours its result', () => {
    expect(eventVerb(event({ kind: 'navigate', method: 'GET' }))).toBe('GET');
    expect(eventVerb(event({ kind: 'request', method: 'POST' }))).toBe('POST');
    expect(eventVerb(event({ kind: 'fill', method: null }))).toBe('fill');
    expect(eventVerb(event({ kind: 'skip', method: null }))).toBe('GET');
    expect(eventResultTone('blocked')).toBe('teal');
    expect(eventResultTone('skipped')).toBe('slate');
    expect(eventResultTone('captured')).toBe('violet');
    expect(eventResultTone('withheld')).toBe('amber');
    expect(eventResultTone('failed')).toBe('rose');
    expect(eventResultTone('200')).toBeNull();
  });
  it('formats elapsed time', () => {
    expect(fmtElapsed(252)).toBe('4:12');
    expect(fmtElapsed(5)).toBe('0:05');
    expect(fmtElapsed(3725)).toBe('1:02:05');
    expect(fmtElapsed(null)).toBe('0:00');
  });
});
