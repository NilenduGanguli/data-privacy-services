import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * A modal confirmation on the native `<dialog>` (opened with `showModal()` when `open` turns true).
 * With `requireText`, the confirm button stays disabled until the typed text equals it (the
 * in-place/append pattern of contract §14). Escape and a click on the backdrop cancel.
 */
export function ConfirmDialog({ open, title, body, confirmLabel, danger, requireText, busy, error, children, onConfirm, onCancel }: {
  open: boolean; title: string; body: ReactNode; confirmLabel: string; danger?: boolean; requireText?: string; busy?: boolean;
  error?: string | null; children?: ReactNode; onConfirm: () => void; onCancel: () => void;
}): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState('');
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setTyped('');
      if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    } else if (!open && dlg.open) dlg.close();
  }, [open]);
  const blocked = !!requireText && typed !== requireText;
  return (
    <dialog ref={ref} className="cdlg" aria-labelledby="cdlg-title"
      onCancel={(e) => { e.preventDefault(); onCancel(); }}
      onClick={(e) => { if (e.target === ref.current) onCancel(); }}>
      {open && (
        <form method="dialog" onSubmit={(e) => { e.preventDefault(); if (!blocked && !busy) onConfirm(); }}>
          <h2 id="cdlg-title">{title}</h2>
          <div className="cdlg-body">{body}</div>
          {children}
          {requireText && (
            <label className="cdlg-type"><span>Type <span className="mono">{requireText}</span> to confirm</span>
              <input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} /></label>
          )}
          {error && <div className="err" role="alert">{error}</div>}
          <div className="cdlg-acts">
            <button className="btn" type="button" onClick={onCancel}>Cancel</button>
            <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} type="submit" disabled={blocked || busy}>{confirmLabel}</button>
          </div>
        </form>
      )}
    </dialog>
  );
}
