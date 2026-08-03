// Shared button styling for the KD nodes.
//
// A stylesheet rather than inline styles, because :hover and :disabled are
// pseudo-classes and element.style can't express them. Injected once; importing
// this module has no other side effects (it registers no extension).

const ROW_CSS =
    "display:flex;gap:6px;width:100%;align-items:center;" +
    "box-sizing:border-box;padding:2px 0;";

export function ensureButtonStyle() {
    if (document.getElementById("kd_button_style")) return;
    const style = document.createElement("style");
    style.id = "kd_button_style";
    style.textContent =
        ".kd-btn{flex:1 1 0;min-width:0;padding:0 8px;cursor:pointer;" +
        "border:1px solid #555;background:#333;color:#ddd;border-radius:4px;" +
        "font-size:12px;transition:background .08s,border-color .08s,color .08s;" +
        // Fixed height and no wrapping: a narrow node would otherwise break a
        // long label onto two lines and stretch the whole row.
        "height:26px;line-height:26px;white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis;}" +
        ".kd-btn:hover:not(:disabled){background:#404040;border-color:#777;" +
        "color:#fff;}" +
        ".kd-btn:active:not(:disabled){background:#2a2a2a;border-color:#888;}" +
        ".kd-btn:disabled{cursor:default;color:#666;border-color:#3a3a3a;" +
        "background:#2b2b2b;}";
    document.head.appendChild(style);
}

export function makeButton(label) {
    ensureButtonStyle();
    const b = document.createElement("button");
    b.className = "kd-btn";
    b.textContent = label;
    return b;
}

// A real <button> blocks its own clicks when disabled; the stylesheet handles
// how it looks.
export const setEnabled = (btn, on) => { btn.disabled = !on; };

// Momentary label change rather than an alert, so a failure doesn't interrupt.
export function flash(btn, text, ms = 700) {
    const original = btn.dataset.label || btn.textContent;
    btn.dataset.label = original;
    btn.textContent = text;
    clearTimeout(btn._flashTimer);
    btn._flashTimer = setTimeout(() => { btn.textContent = original; }, ms);
}

// One DOM widget holding a row of buttons. Needed because a LiteGraph
// addWidget("button") always takes a full row to itself, so two can't share a
// line — and it can't be styled.
export function addButtonRow(node, id, buttons, height = 32) {
    ensureButtonStyle();
    const row = document.createElement("div");
    row.style.cssText = ROW_CSS;
    row.append(...buttons);
    const widget = node.addDOMWidget(id, "div", row, {
        serialize: false,
        getMinHeight: () => height,
    });
    widget.computeSize = () => [0, height];
    return widget;
}
