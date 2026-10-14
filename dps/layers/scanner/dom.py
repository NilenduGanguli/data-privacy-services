"""Page-side JavaScript for the scanner and typed wrappers around it.

Everything these snippets return is page data and stays in worker memory; callers persist only
what classification and redaction derive from it (the facts models hide it from ``repr``).

Ordering contract: frames are numbered ``0`` (main) then every same-origin child frame in
``page.frames`` order; fields are numbered ``1..n`` across frames in that order and in tree order
inside each frame, descending into open shadow roots at their host. A snapshot also lists every
rendered child frame it did not read (``FrameFacts.read=False``): a same-origin frame that failed
while it was read keeps its number (and is listed in ``PageSnapshot.frames_failed``), and every
other child frame (cross-origin, or any child frame when ``iframes`` is off) is numbered after the
last ``frame_map`` index. Their content is in the screenshot but in no finding, so captures black
out the boxes of frames never read and are withheld when a frame failed (``unread_frame_boxes``). ``EXTRACT_JS`` stamps each
field with ``data-dps-field="<frame>:<n>"`` and each clickable with ``data-dps-click="<frame>:<k>"``;
a fresh load of the same page re-runs the same script and gets the same numbers.
``CLICKABLES_JS`` re-stamps the clickables alone with the same predicate and order.

Text nodes are read whole up to ``TEXT_READ_CAP`` (a longer node marks the snapshot ``truncated``,
which withholds every capture of the page). Captures do not rely on the classification text list
alone: ``CENSUS_JS`` (``render_census``) lists every string a read frame renders at capture time
(button text, listbox options and input button labels included), measured by ``census_rects``.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

from dps.layers.scanner.models import (
    ClickableFacts,
    FieldFacts,
    FormFacts,
    FrameFacts,
    LinkFacts,
    PageSnapshot,
    Rect,
    TextFacts,
)

#: A text node is read whole up to this many UTF-16 code units. A longer one is read up to the cap
#: for classification, and the extraction is marked ``truncated``, so no capture of the page is
#: emitted (its tail is rendered but in no finding; M4-G3 review r2 B3).
TEXT_READ_CAP = 20_000
#: The text a frame's extraction (or one census of it) reads in all, in UTF-16 code units; past it
#: the extraction is ``truncated`` and a census fails closed.
TEXT_READ_BUDGET = 2_000_000

#: Whether a text node is drawn, decided from the node (M4 verify r1 B4): its parent element is
#: visible, or — a parent with no box of its own (``display:contents``: a ``<slot>``, a component
#: host styled ``:host{display:contents}``, any element so styled) that is not hidden — a range
#: over the node itself has client rects. Shared by the extraction (``textOk``) and ``CENSUS_JS``.
_RENDERED_JS = r"""
  const rendered = (node, parent, visibleFn) => {
    if (visibleFn(parent)) return true;
    const st = getComputedStyle(parent);
    if (st.display === 'none' || st.visibility === 'hidden' || st.visibility === 'collapse') return false;
    const range = document.createRange();
    try { range.selectNodeContents(node); } catch (e) { return false; }
    return range.getClientRects().length > 0;
  };
"""

#: Shared by ``EXTRACT_JS`` and ``LIVE_TEXT_JS``: which text nodes count as rendered text for
#: classification, and the row/column header, ``<dt>`` or ``Label:`` hint next to one.
_TEXT_HELPERS_JS = (
    (
        r"""
  const TEXT_CAP = __TEXT_CAP__, TEXT_BUDGET = __TEXT_BUDGET__;
  const SKIP_TEXT = 'script,style,noscript,template,select,option,textarea,button';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (el) => clean(el ? (el.innerText || el.textContent) : '');
  const visible = (el) => {
    if (!(el instanceof Element)) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && el.getClientRects().length > 0;
  };
"""
        + _RENDERED_JS
        + r"""
  const textOk = (node) => {
    const parent = node.parentElement;
    return !!parent && clean(node.nodeValue || '').length >= 2 && !parent.closest(SKIP_TEXT) && rendered(node, parent, visible);
  };
  const inTable = (el) => !!el.closest('table,[role=grid],[role=table]');
  const labelHint = (el) => {
    const cell = el.closest('td,[role=cell],[role=gridcell]');
    if (cell) {
      const row = cell.parentElement;
      const th = row ? row.querySelector('th,[role=rowheader]') : null;
      if (th && th !== cell) return text(th);
      const table = cell.closest('table');
      const head = table ? table.querySelector('thead tr') : null;
      if (head && row) { const idx = Array.prototype.indexOf.call(row.children, cell); if (head.children[idx]) return text(head.children[idx]); }
    }
    const dd = el.closest('dd');
    if (dd) { let p = dd.previousElementSibling; while (p && p.tagName !== 'DT') p = p.previousElementSibling; if (p) return text(p); }
    const prev = el.previousElementSibling;
    if (prev && /:\s*$/.test(text(prev)) && text(prev).length <= 40) return text(prev).replace(/:\s*$/, '');
    return null;
  };
"""
    )
    .replace("__TEXT_CAP__", str(TEXT_READ_CAP))
    .replace("__TEXT_BUDGET__", str(TEXT_READ_BUDGET))
)

EXTRACT_JS = (
    r"""
(args) => {
  const { frameIndex, shadow, startField, startForm, maxFields, token } = args;
"""
    + _TEXT_HELPERS_JS
    + r"""
  const NON_FIELD = new Set(['submit', 'button', 'reset', 'image']);
  const CLICKABLE = '[role="link"]:not([href]), a[href^="#"], a[href^="javascript:" i]';
  const byId = (el, id) => {
    const root = el.getRootNode();
    return (root && root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
  };
  const withoutControls = (label) => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button').forEach((n) => n.remove());
    return text(copy);
  };
  // Nearest text = the closest preceding text with NO other control in between (text that sits
  // before another control belongs to that control). Climbs at most three levels.
  const isControl = (s) => s.matches('input,select,textarea,button') || !!s.querySelector('input,select,textarea,button');
  const nearestText = (el) => {
    let n = el;
    for (let depth = 0; depth < 3 && n; depth += 1) {
      let s = n.previousElementSibling;
      while (s) {
        if (isControl(s)) return '';
        const t = text(s);
        if (t && t.length <= 80) return t;
        s = s.previousElementSibling;
      }
      n = n.parentElement;
    }
    return '';
  };
  const resolveLabel = (el) => {
    const id = el.getAttribute('id');
    const root = el.getRootNode();
    if (id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l && text(l)) return [text(l), 'label_for'];
    }
    const wrap = el.closest('label');
    if (wrap) { const t = withoutControls(wrap); if (t) return [t, 'label_wrap']; }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = clean(lb.split(/\s+/).map((x) => byId(el, x)).filter(Boolean).map(text).join(' '));
      if (t) return [t, 'aria_labelledby'];
    }
    const al = clean(el.getAttribute('aria-label')); if (al) return [al, 'aria_label'];
    const ph = clean(el.getAttribute('placeholder')); if (ph) return [ph, 'placeholder'];
    const near = nearestText(el); if (near) return [near, 'nearest_text'];
    const ti = clean(el.getAttribute('title')); if (ti) return [ti, 'title'];
    return ['', 'none'];
  };
  const formOf = (el) => {
    if (el.form) return el.form;
    const direct = el.closest('form');
    if (direct) return direct;
    const host = el.getRootNode() && el.getRootNode().host;
    return host ? host.closest('form') : null;
  };
  const describe = (el, inShadow) => {
    const tag = el.tagName.toLowerCase();
    const id = el.getAttribute('id'); const name = el.getAttribute('name');
    const own = tag + (id ? `#${id}` : '') + (name ? `[name="${name}"]` : '');
    const host = inShadow && el.getRootNode().host ? el.getRootNode().host.tagName.toLowerCase() + ' >>> ' : '';
    return host + own;
  };
  window.__dpsTextNodes = [];
  window.__dpsTextValues = [];
  window.__dpsSnap = null;
  const fields = [], forms = new Map(), texts = [], links = [], clickables = [];
  let truncated = false, clickOrdinal = 0, budget = TEXT_BUDGET;
  const sx = window.scrollX, sy = window.scrollY;
  const onField = (el, inShadow) => {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase()
      : tag === 'select' ? (el.multiple ? 'select-multiple' : 'select-one') : 'textarea';
    if (tag === 'input' && NON_FIELD.has(type)) return;
    if (fields.length >= maxFields) { truncated = true; return; }
    const n = startField + fields.length + 1;
    const key = `${frameIndex}:${n}`;
    el.setAttribute('data-dps-field', key);
    let formIndex = 0;
    const form = formOf(el);
    if (form) {
      if (!forms.has(form)) forms.set(form, { form_index: startForm + forms.size + 1, method: (form.getAttribute('method') || 'get').toLowerCase(),
        action: form.getAttribute('action') || '', frame_index: frameIndex, in_shadow: inShadow, field_count: 0 });
      const f = forms.get(form); f.field_count += 1; formIndex = f.form_index;
    }
    const [label, source] = resolveLabel(el);
    const r = el.getBoundingClientRect();
    const secret = type === 'password' || type === 'file' || type === 'checkbox' || type === 'radio';
    fields.push({ key, field_no: n, form_index: formIndex, frame_index: frameIndex, in_shadow: inShadow, tag, input_type: type,
      name: el.getAttribute('name'), id: el.getAttribute('id'), label: label.slice(0, 200), label_source: source,
      placeholder: el.getAttribute('placeholder'), title: el.getAttribute('title'), aria_label: el.getAttribute('aria-label'),
      autocomplete: el.getAttribute('autocomplete'), pattern: el.getAttribute('pattern'),
      maxlength: el.maxLength > 0 ? el.maxLength : null, minlength: el.minLength > 0 ? el.minLength : null,
      required: el.required === true, readonly: el.readOnly === true, disabled: el.disabled === true,
      visible: type !== 'hidden' && visible(el), inputmode: el.getAttribute('inputmode'),
      options: tag === 'select' ? Array.from(el.options).slice(0, 300).map((o) => ({ value: o.value, text: clean(o.text), disabled: o.disabled, selected: o.selected })) : [],
      value: secret || tag === 'select' ? null : (el.value || null),
      checked: type === 'checkbox' || type === 'radio' ? el.checked : null,
      rect: { x: r.left + sx, y: r.top + sy, w: r.width, h: r.height }, selector: describe(el, inShadow) });
  };
  const onText = (node) => {
    if (!textOk(node)) return;
    if (texts.length >= 5000) { truncated = true; return; }
    const parent = node.parentElement;
    const full = node.nodeValue || '';
    // a node longer than the cap is read up to it; its tail is in no finding (no capture)
    if (full.length > TEXT_CAP) truncated = true;
    const raw = full.length > TEXT_CAP ? full.slice(0, TEXT_CAP) : full;
    budget -= raw.length;
    if (budget < 0) { truncated = true; return; }
    const i = window.__dpsTextNodes.length;
    window.__dpsTextNodes.push(node);
    window.__dpsTextValues.push(full);  // the whole node: the census compares the whole node
    texts.push({ frame_index: frameIndex, i, text: raw, in_table: inTable(parent), label_hint: labelHint(parent) });
  };
  const walk = (root, inShadow) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = it.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) onText(node);
      else {
        const el = node;
        if (el.matches('input,select,textarea')) onField(el, inShadow);
        else if (el.matches('a[href]') && !/^(javascript:|#)/i.test(el.getAttribute('href') || '')) {
          if (links.length < 500) links.push({ href: el.href, in_form: !!el.closest('form'), download: el.hasAttribute('download') });
        }
        if (el.matches(CLICKABLE) && !el.closest('form') && visible(el)) {
          const kind = el.matches('[role="link"]:not([href])') ? 'role_link' : (el.getAttribute('href') || '').startsWith('#') ? 'hash_link' : 'js_link';
          clickOrdinal += 1;
          el.setAttribute('data-dps-click', `${frameIndex}:${clickOrdinal}`);
          clickables.push({ frame_index: frameIndex, ordinal: clickOrdinal, kind });
        }
        if (shadow && el.shadowRoot && el.shadowRoot.mode === 'open') walk(el.shadowRoot, true);
      }
      node = it.nextNode();
    }
  };
  const top = document.body || document.documentElement;
  if (top) walk(top, false);
  // the stamp captures check: this frame was read by THIS extraction, under THIS number
  window.__dpsSnap = { id: token, frameIndex, shadow, base: window.__dpsTextNodes.length };
  return { title: clean(document.title), fields, forms: Array.from(forms.values()), texts, links, clickables, truncated };
}
"""
)

STAMP_JS = r"""
([token, frameIndex]) => !!window.__dpsSnap && window.__dpsSnap.id === token && window.__dpsSnap.frameIndex === frameIndex
"""

#: Text the frame renders NOW that its extraction did not read: every qualifying text node that is
#: not among the extraction's nodes (appended to them, so it can be measured) or that was appended
#: by an earlier call, and every extracted node whose text changed (compared whole, not a prefix).
#: ``null``: the frame is not the one the extraction read (stamp), so nothing about it can be
#: trusted, or it renders more text than can be checked (a node past ``TEXT_READ_CAP``, more than
#: 5000 such nodes, more than ``TEXT_READ_BUDGET`` in all).
LIVE_TEXT_JS = (
    r"""
(token) => {
"""
    + _TEXT_HELPERS_JS
    + r"""
  const snap = window.__dpsSnap;
  const nodes = window.__dpsTextNodes, values = window.__dpsTextValues;
  if (!snap || snap.id !== token || !Array.isArray(nodes) || !Array.isArray(values)) return null;
  const known = new Map();
  nodes.forEach((n, i) => known.set(n, i));
  const items = [];
  let overflow = false, budget = TEXT_BUDGET;
  const visit = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = it.nextNode();
    while (node && !overflow) {
      if (node.nodeType === Node.TEXT_NODE) {
        if (textOk(node)) {
          const raw = node.nodeValue || '';
          if (raw.length > TEXT_CAP) { overflow = true; break; }
          let i = known.get(node);
          if (i === undefined) { i = nodes.length; nodes.push(node); values.push(raw); known.set(node, i); }
          if (i >= snap.base || values[i] !== raw) {
            budget -= raw.length;
            if (items.length >= 5000 || budget < 0) { overflow = true; break; }
            const parent = node.parentElement;
            items.push({ i, text: raw, changed: i < snap.base, in_table: inTable(parent), label_hint: labelHint(parent) });
          }
        }
      } else if (snap.shadow && node.shadowRoot && node.shadowRoot.mode === 'open') {
        visit(node.shadowRoot);
      }
      node = it.nextNode();
    }
  };
  const top = document.body || document.documentElement;
  if (top) visit(top);
  return overflow ? null : items;
}
"""
)

CLICKABLES_JS = r"""
(arg) => {   // re-stamp data-dps-click on a fresh load without a full extract (same ordinals)
  // arg: {frameIndex, shadow} (or a bare frame index, which descends into open shadow roots)
  const frameIndex = typeof arg === 'number' ? arg : arg.frameIndex;
  const shadow = typeof arg === 'number' ? true : arg.shadow !== false;
  const CLICKABLE = '[role="link"]:not([href]), a[href^="#"], a[href^="javascript:" i]';
  const visible = (el) => {
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && el.getClientRects().length > 0;
  };
  let k = 0;
  const visit = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let el = it.nextNode();
    while (el) {
      if (el.matches(CLICKABLE) && !el.closest('form') && visible(el)) {
        k += 1; el.setAttribute('data-dps-click', `${frameIndex}:${k}`);
      }
      if (shadow && el.shadowRoot && el.shadowRoot.mode === 'open') visit(el.shadowRoot);
      el = it.nextNode();
    }
  };
  const top = document.body || document.documentElement;
  if (top) visit(top);
  return k;
}
"""

CLICK_TARGET_JS = r"""
(el) => {   // the clickable's resolved target: null = no href (role="link"), '' = unresolvable
  const raw = el.getAttribute('href') ?? el.getAttributeNS('http://www.w3.org/1999/xlink', 'href');
  if (raw === null) return null;
  try { return new URL(raw, el.baseURI).href; } catch (e) { return ''; }
}
"""

TEXT_RECTS_JS = r"""
(spans) => spans.map(([i, start, end]) => {
  const node = (window.__dpsTextNodes || [])[i];
  if (!node) return [];
  const range = document.createRange();
  try { range.setStart(node, start); range.setEnd(node, end); } catch (e) { return []; }
  return Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height }));
})
"""

#: Rewrite extracted text nodes whole: a node is replaced only while it still holds exactly the text
#: the extraction read (so no unread tail or later change is dropped or kept by mistake); returns
#: how many were replaced (the caller withholds unless all of them were).
REPLACE_TEXT_JS = r"""
(pairs) => {
  const nodes = window.__dpsTextNodes || [], values = window.__dpsTextValues || [];
  let n = 0;
  for (const [i, t] of pairs) {
    const node = nodes[i];
    if (node && node.nodeValue === values[i]) { node.nodeValue = t; values[i] = t; n += 1; }
  }
  return n;
}
"""

#: The capture's census of every string a read frame renders NOW, separate from (and wider than)
#: the classification text list: every rendered text node (``rendered``: a visible parent, or one
#: with no box of its own whose text has client rects) with no minimum length and no ``button``
#: exclusion (only ``script``/``style``/``noscript``/``template``/``title`` and a ``textarea``'s
#: source text, whose shown value is a field, are left out), every visible ``<option>`` (its text
#: and its ``label``; a listbox shows its options, a closed select's are not rendered and its shown
#: option is swept as a field), and the strings the browser draws from attributes of a visible
#: element (M4 verify r1 B5): the label of a button-type ``<input>``, an ``<optgroup>`` label, the
#: ``alt`` of an ``<img>`` or ``<input type=image>`` (shown when the image is broken), and the
#: placeholder of an empty ``<input>`` or ``<textarea>``.
#: Open shadow roots are always descended (whether or not the extraction read them). Text nodes are
#: measured by range (``box: false``); options and input labels by their element box
#: (``box: true``). ``known``: a text node the classification list holds (extracted or late).
#: ``null``: the frame is not the one the extraction read, or it renders more than can be checked.
CENSUS_JS = (
    (
        r"""
(token) => {
  const snap = window.__dpsSnap;
  if (!snap || snap.id !== token) return null;
"""
        + r"""
  const TEXT_CAP = __TEXT_CAP__, TEXT_BUDGET = __TEXT_BUDGET__;
  const SKIP = 'script,style,noscript,template,title,textarea,select';
  const LABELLED = new Set(['button', 'submit', 'reset']);
  const shown = new Map();
  const visible = (el) => {
    if (!shown.has(el)) {
      const st = getComputedStyle(el);
      shown.set(el, st.visibility !== 'hidden' && st.display !== 'none' && el.getClientRects().length > 0);
    }
    return shown.get(el);
  };
"""
        + _RENDERED_JS
        + r"""
  const known = new Set(window.__dpsTextNodes || []);
  const targets = [], items = [];
  let over = false, budget = TEXT_BUDGET;
  const add = (target, text, box) => {
    if (!text || !/\S/.test(text)) return;
    budget -= text.length;
    if (text.length > TEXT_CAP || budget < 0 || items.length >= 50000) { over = true; return; }
    items.push({ k: targets.length, text, box, known: !box && known.has(target) });
    targets.push(target);
  };
  const visit = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = it.nextNode();
    while (node && !over) {
      if (node.nodeType === Node.TEXT_NODE) {
        const parent = node.parentElement;
        if (parent && !parent.closest(SKIP) && rendered(node, parent, visible)) add(node, node.nodeValue, false);
      } else {
        const el = node, tag = el.localName;
        if (tag === 'option') {
          // a listbox option shows its label attribute when it has one, else its text
          if (visible(el)) { add(el, el.text, true); if (el.label !== el.text) add(el, el.label, true); }
        } else if (tag === 'optgroup') {
          if (visible(el)) add(el, el.label, true);  // a listbox draws the group's label
        } else if (tag === 'img') {
          if (visible(el)) add(el, el.alt, true);  // drawn when the image is broken or loading
        } else if (tag === 'input') {
          const type = (el.getAttribute('type') || 'text').toLowerCase();
          if (LABELLED.has(type)) { if (visible(el)) add(el, el.value, true); }
          else if (type === 'image') { if (visible(el)) add(el, el.alt, true); }
          else if (!el.value && visible(el)) add(el, el.placeholder, true);
        } else if (tag === 'textarea') {
          if (!el.value && visible(el)) add(el, el.placeholder, true);
        }
        if (el.shadowRoot && el.shadowRoot.mode === 'open') visit(el.shadowRoot);
      }
      node = it.nextNode();
    }
  };
  const top = document.body || document.documentElement;
  if (top) visit(top);
  window.__dpsCensus = targets;
  return over ? null : items;
}
"""
    )
    .replace("__TEXT_CAP__", str(TEXT_READ_CAP))
    .replace("__TEXT_BUDGET__", str(TEXT_READ_BUDGET))
)

#: Client rects (page coordinates) of ``[k, start, end]`` census spans of the last ``CENSUS_JS``
#: call: a range for a text node (UTF-16 offsets), the whole element box for an element.
CENSUS_RECTS_JS = r"""
(spans) => spans.map(([k, start, end]) => {
  const t = (window.__dpsCensus || [])[k];
  if (!t) return [];
  const sx = window.scrollX, sy = window.scrollY;
  let rects;
  if (t.nodeType === Node.TEXT_NODE) {
    const range = document.createRange();
    try { range.setStart(t, start); range.setEnd(t, end); } catch (e) { return []; }
    rects = Array.from(range.getClientRects());
  } else {
    if (!t.isConnected) return [];
    rects = [t.getBoundingClientRect()];
  }
  return rects.filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x: r.left + sx, y: r.top + sy, w: r.width, h: r.height }));
})
"""

SET_VALUES_JS = r"""
(pairs) => {   // set input values WITHOUT dispatching events (captures must not trigger autosave)
  let n = 0;
  for (const [key, value] of pairs) {
    const el = document.querySelector(`[data-dps-field="${key}"]`) || (function find(root) {
      for (const host of root.querySelectorAll('*')) { if (host.shadowRoot) { const hit = host.shadowRoot.querySelector(`[data-dps-field="${key}"]`) || find(host.shadowRoot); if (hit) return hit; } }
      return null; })(document);
    if (el) { el.value = value; n += 1; }
  }
  return n;
}
"""

VALIDATION_JS = r"""
(el) => {
  const out = [];
  const CODES = ['valueMissing','typeMismatch','patternMismatch','tooLong','tooShort','rangeUnderflow','rangeOverflow','stepMismatch','badInput','customError'];
  if (el.willValidate && !el.checkValidity()) {
    out.push({ source: 'native', codes: CODES.filter((c) => el.validity[c]), text: (el.validationMessage || '').slice(0, 200) });
  }
  const root = el.getRootNode();
  const ids = `${el.getAttribute('aria-describedby') || ''} ${el.getAttribute('aria-errormessage') || ''}`.split(/\s+/).filter(Boolean);
  for (const id of ids) {
    const n = (root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
    const t = n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : '';
    if (t && n.getClientRects().length > 0) out.push({ source: 'aria', codes: [], text: t.slice(0, 200) });
  }
  if (el.getAttribute('aria-invalid') === 'true') out.push({ source: 'aria-invalid', codes: [], text: '' });
  return out;
}
"""

SUBMIT_ATTEMPTS_JS = "() => (window.__dpsGuard ? window.__dpsGuard.submitAttempts : 0)"
SCROLL_JS = (
    "() => { window.scrollTo(0, 0); "
    "return [window.scrollX, window.scrollY, document.documentElement.scrollHeight]; }"
)

_NO_OFFSET = Rect(x=0, y=0, w=0, h=0)


@dataclass(frozen=True)
class Extraction:
    """The live identity of one ``extract_page`` call (memory only, bound to its snapshot).

    ``frames`` maps every frame number the snapshot read to the very ``Frame`` object it read
    (identity, not position: a frame that attaches or detaches later never takes another frame's
    number), and ``token`` is stamped into each of them (``window.__dpsSnap``) so a capture can tell
    that a frame still shows the document this extraction read (not reloaded, navigated or read
    again by a later extraction).
    """

    token: str
    frames: Mapping[int, Frame]
    iframes: bool
    shadow_dom: bool


def extraction_of(snapshot: PageSnapshot) -> Extraction | None:
    """The live identity of ``snapshot``'s extraction (``None``: not from ``extract_page``)."""
    ext = snapshot.extraction
    return ext if isinstance(ext, Extraction) else None


async def stamped(frame: Frame, token: str, frame_index: int) -> bool:
    """Whether ``frame`` still shows the document extraction ``token`` read as ``frame_index``."""
    try:
        return bool(await frame.evaluate(STAMP_JS, [token, frame_index]))
    except PlaywrightError:
        return False


async def late_texts(ext: Extraction) -> list[tuple[TextFacts, bool]] | None:
    """Text the read frames render now that the extraction did not read: ``(facts, changed)`` —
    ``changed`` for an extracted node whose text is different now, else a node the extraction
    never saw (``facts.i`` numbers it after the extracted ones, so ``text_rects`` can measure it).
    ``None`` (fail closed): a frame is not the one the extraction read, failed, or renders more new
    text than can be checked."""
    out: list[tuple[TextFacts, bool]] = []
    for index, frame in ext.frames.items():
        try:
            raw = await frame.evaluate(LIVE_TEXT_JS, ext.token)
        except PlaywrightError:
            return None
        if raw is None:
            return None
        for item in raw:
            changed = bool(item.pop("changed"))
            out.append((TextFacts.model_validate({**item, "frame_index": index}), changed))
    return out


@dataclass(frozen=True)
class CensusItem:
    """One string a read frame renders, from a capture's census (``CENSUS_JS``; memory only, the
    text never in ``repr``). ``k`` numbers it in its frame's last census; ``box``: measured as its
    element's whole box (a listbox option, an input button label) rather than by text range;
    ``known``: a text node the classification text list holds."""

    frame_index: int
    k: int
    text: str = field(repr=False)
    box: bool
    known: bool


async def render_census(ext: Extraction) -> list[CensusItem] | None:
    """Every string the read frames of ``ext`` render now (``CENSUS_JS``), frame by frame in
    document order. ``None`` (fail closed): a frame is not the one the extraction read, failed, or
    renders more text than can be checked."""
    out: list[CensusItem] = []
    for index, frame in ext.frames.items():
        try:
            raw = await frame.evaluate(CENSUS_JS, ext.token)
        except PlaywrightError:
            return None
        if raw is None:
            return None
        out += [
            CensusItem(
                frame_index=index,
                k=int(item["k"]),
                text=str(item["text"]),
                box=bool(item["box"]),
                known=bool(item["known"]),
            )
            for item in raw
        ]
    return out


async def census_rects(
    frames: Mapping[int, Frame],
    offsets: Mapping[int, Rect],
    items: list[CensusItem],
    spans: list[tuple[int, int, int]],
) -> list[list[Rect]]:
    """Client rects (document coordinates of the top page) of ``(item, start, end)`` spans over
    ``items`` (positions in that list; code-point offsets into the item's text, ignored for a
    ``box`` item, which is measured whole), in the order given. ``offsets``: each read frame's box
    now. A span that cannot be measured gets ``[]``. One evaluation per frame."""
    out: list[list[Rect]] = [[] for _ in spans]
    per_frame: dict[int, list[tuple[int, list[int]]]] = {}
    for n, (pos, start, end) in enumerate(spans):
        item = items[pos]
        per_frame.setdefault(item.frame_index, []).append(
            (n, [item.k, utf16_offset(item.text, start), utf16_offset(item.text, end)])
        )
    for frame_index, todo in per_frame.items():
        frame = frames.get(frame_index)
        if frame is None:
            continue
        try:
            raw = await frame.evaluate(CENSUS_RECTS_JS, [js for _, js in todo])
        except PlaywrightError:
            continue
        off = offsets.get(frame_index, _NO_OFFSET)
        for (n, _), rects in zip(todo, raw, strict=True):
            out[n] = [Rect(x=r["x"] + off.x, y=r["y"] + off.y, w=r["w"], h=r["h"]) for r in rects]
    return out


def utf16_offset(text: str, index: int) -> int:
    """Python code-point offset -> JavaScript UTF-16 code-unit offset (astral characters count 2).

    A lone surrogate (text the page cut inside a pair) counts 1, as in JavaScript, and never raises.
    """
    return len(text[:index].encode("utf-16-le", errors="surrogatepass")) // 2


def _origin(url: str) -> tuple[str, str, int | None] | None:
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError:  # a malformed port: not comparable, so not same-origin
        return None
    return (p.scheme, (p.hostname or "").lower(), port)


async def frame_map(page: Page, *, iframes: bool) -> dict[int, Frame]:
    """frame_index -> Frame: 0 = main; then same-origin (or about:blank/srcdoc) child frames."""
    out: dict[int, Frame] = {0: page.main_frame}
    if not iframes:
        return out
    main = _origin(page.main_frame.url)
    for frame in page.frames:
        if frame is page.main_frame or frame.is_detached():
            continue
        if frame.url.startswith("about:") or (main is not None and _origin(frame.url) == main):
            out[len(out)] = frame
    return out


async def _frame_offset(frame: Frame, scroll: tuple[float, float]) -> Rect:
    if frame.parent_frame is None:
        return _NO_OFFSET
    element = await frame.frame_element()
    box = await element.bounding_box()  # relative to the main viewport
    if box is None:
        return _NO_OFFSET
    return Rect(x=box["x"] + scroll[0], y=box["y"] + scroll[1], w=box["width"], h=box["height"])


async def _unread_box(frame: Frame, scroll: tuple[float, float]) -> Rect | None:
    """An unread frame's box in document coordinates (zero-size: not rendered; ``None``: unknown)."""
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
    except PlaywrightError:
        return None
    if box is None:  # not rendered (display:none, detached)
        return _NO_OFFSET
    return Rect(x=box["x"] + scroll[0], y=box["y"] + scroll[1], w=box["width"], h=box["height"])


async def extract_page(
    page: Page, *, iframes: bool, shadow_dom: bool, max_fields: int
) -> PageSnapshot:
    """Fields, forms, rendered text, links and clickables of ``page`` (memory only).

    A child frame that fails while it is read (it detached or navigated) contributes nothing; it
    is recorded in ``frames_failed`` (fail closed: no masked capture of the page) and listed with
    ``read=False`` and its box while it is still on the page, like every rendered child frame that
    is not read at all (cross-origin, or any child frame when ``iframes`` is off), whose box a
    capture must black out. The main frame's errors propagate.
    """
    frames = await frame_map(page, iframes=iframes)
    token = secrets.token_hex(12)
    sx, sy, _ = await page.evaluate(SCROLL_JS)
    fields: list[FieldFacts] = []
    forms: list[FormFacts] = []
    texts: list[TextFacts] = []
    links: list[LinkFacts] = []
    clicks: list[ClickableFacts] = []
    frame_facts: list[FrameFacts] = []
    unread: list[tuple[int, Frame]] = []
    failed: list[int] = []
    truncated, title = False, ""
    for index, frame in frames.items():
        args = {
            "frameIndex": index,
            "shadow": shadow_dom,
            "startField": len(fields),
            "startForm": len(forms),
            "maxFields": max(0, max_fields - len(fields)),
            "token": token,
        }
        try:
            offset = await _frame_offset(frame, (sx, sy))
            raw: dict[str, Any] = await frame.evaluate(EXTRACT_JS, args)
        except PlaywrightError:
            if index == 0:
                raise
            failed.append(index)
            unread.append((index, frame))
            continue
        frame_facts.append(
            FrameFacts(
                frame_index=index,
                same_origin=True,
                offset=offset,
                path="main" if index == 0 else (frame.name or f"frame{index}"),
            )
        )
        if index == 0:
            title = raw["title"]
        for f in raw["fields"]:
            if index and f.get("rect"):
                f["rect"] = {
                    **f["rect"],
                    "x": f["rect"]["x"] + offset.x,
                    "y": f["rect"]["y"] + offset.y,
                }
            fields.append(FieldFacts.model_validate(f))
        forms += [FormFacts.model_validate(f) for f in raw["forms"]]
        texts += [TextFacts.model_validate(t) for t in raw["texts"]]
        links += [LinkFacts.model_validate(link) for link in raw["links"]]
        clicks += [ClickableFacts.model_validate(c) for c in raw["clickables"]]
        truncated = truncated or bool(raw["truncated"])
    listed = set(frames.values())
    next_index = max(frames) + 1
    for frame in page.frames:
        if frame is page.main_frame or frame in listed:
            continue
        unread.append((next_index, frame))
        next_index += 1
    main = _origin(page.main_frame.url)
    for index, frame in unread:
        box = await _unread_box(frame, (sx, sy))
        if box is None and frame.is_detached():
            continue  # gone from the page: nothing of it is rendered
        frame_facts.append(
            FrameFacts(
                frame_index=index,
                same_origin=frame in listed or (main is not None and _origin(frame.url) == main),
                offset=box,
                path=frame.name or f"frame{index}",
                read=False,
            )
        )
    snapshot = PageSnapshot(
        url=page.url,
        title=title,
        forms=forms,
        fields=fields,
        texts=texts,
        links=links,
        clickables=clicks,
        frames=frame_facts,
        truncated=truncated,
        frames_failed=failed,
    )
    read = {index: frame for index, frame in frames.items() if index not in failed}
    snapshot.bind_extraction(
        Extraction(token=token, frames=read, iframes=iframes, shadow_dom=shadow_dom)
    )
    return snapshot


def unread_frame_boxes(snapshot: PageSnapshot) -> list[Rect] | None:
    """The boxes a capture of ``snapshot``'s page must black out: every rendered child frame that
    was not read (its content is in the screenshot but in no finding). ``None`` — the capture must
    be withheld (fail closed) — when a frame failed while it was read (``frames_failed``) or an
    unread frame cannot be located."""
    if snapshot.frames_failed:
        return None
    boxes: list[Rect] = []
    for frame in snapshot.unread_frames:
        if frame.offset is None:
            return None
        if frame.offset.w > 0 and frame.offset.h > 0:
            boxes.append(frame.offset)
    return boxes


async def text_rects(
    page: Page,
    frames: dict[int, Frame],
    snapshot: PageSnapshot,
    spans: list[tuple[int, int, int, int]],
) -> list[list[Rect]]:
    """Client rects (document coordinates of the top page) for ``(frame_index, i, start, end)``
    spans, in the order given; ``start``/``end`` are code-point offsets into the node text the
    detector saw. A span whose node or frame is gone gets ``[]``. One evaluation per frame."""
    by_key = {(t.frame_index, t.i): t.text for t in snapshot.texts}
    offsets = {f.frame_index: f.offset for f in snapshot.frames if f.read and f.offset}
    out: list[list[Rect]] = [[] for _ in spans]
    per_frame: dict[int, list[tuple[int, list[int]]]] = {}
    for n, (frame_index, i, start, end) in enumerate(spans):
        text = by_key.get((frame_index, i), "")
        per_frame.setdefault(frame_index, []).append(
            (n, [i, utf16_offset(text, start), utf16_offset(text, end)])
        )
    for frame_index, items in per_frame.items():
        frame = frames.get(frame_index)
        if frame is None:
            continue
        try:
            raw = await frame.evaluate(TEXT_RECTS_JS, [js for _, js in items])
        except PlaywrightError:
            continue
        off = offsets.get(frame_index, _NO_OFFSET)
        for (n, _), rects in zip(items, raw, strict=True):
            out[n] = [Rect(x=r["x"] + off.x, y=r["y"] + off.y, w=r["w"], h=r["h"]) for r in rects]
    return out
