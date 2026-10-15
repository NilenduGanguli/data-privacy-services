// <postal-lookup>: a web component with an OPEN shadow root holding the PIN code input.
class PostalLookup extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>label{display:block;font-size:12px;color:#4d5875;margin-bottom:4px}
input{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #c5cde0;border-radius:6px;font:inherit}</style>
<label for="pin">PIN code</label><input id="pin" name="pin" inputmode="numeric" pattern="\\d{6}" maxlength="6" autocomplete="postal-code" required>`;
  }
}
customElements.define('postal-lookup', PostalLookup);
