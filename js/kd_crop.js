import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { insertGapAfter } from "./save_image_kd.js";

// Crop box UI for CropImageKD.
//
// The node writes a PNG of frame 0 to temp on execution and hands us its /view
// coordinates plus the SOURCE dimensions. Everything from there is client-side:
// the box is dragged like Photoshop's crop tool and written straight into the
// x / y / width / height widgets. Dragging executes nothing -- ComfyUI has no
// reactive execution, so the new crop lands on the next queue.
//
// WHY <img> + <svg> AND NOT A <canvas>
//
// The graph's zoom scales a node's DOM subtree with a CSS transform. Browsers
// re-rasterise DOM content under a transform, which is why the number widgets,
// an <img>, and SVG geometry all stay sharp however far you zoom in -- and it
// is why the native LoadImage / PreviewImage previews never need refreshing.
//
// A <canvas> is the exception. Its backing store is a fixed bitmap the page
// owns, and the transform can only stretch it: whatever resolution it was last
// drawn at is the resolution you keep. Sizing that store to the zoom "fixes" it
// only for as long as the zoom is unchanged, and needs a reliable signal on
// every zoom step to stay fixed -- a signal LiteGraph does not dependably give
// a DOM widget. That is the stale, resolution-locked overlay.
//
// So there is no bitmap here at all. The picture is an <img> and the overlay is
// SVG geometry in source-pixel coordinates, both re-rasterised by the browser
// at whatever size the graph is currently showing them. Nothing to invalidate,
// nothing to keep in sync, and no zoom plumbing anywhere in this file.

const SVG_NS = "http://www.w3.org/2000/svg";

const MIN_CROP = 8;        // source px; matches MIN_CROP in crop_kd.py
const HANDLE_HIT = 11;     // css px grab radius at the corners
const EDGE_HIT = 7;        // css px grab band along the edges
const HANDLE_L = 16;       // css px arm length of a corner bracket
// Stroke widths, mirroring the .kd-box and .kd-grip rules below. A stroke is
// centred on its path, so half of each is how far that element has to be pulled
// in to sit flush inside the frame. Keep these in step with the CSS.
const OUTLINE_W = 1;       // css px
const GRIP_W = 3;          // css px
const RATIO_H = 26;        // the ratio bar sitting above the image
const STAGE_GAP = 4;       // extra css px above and below the image
// Everything in the widget that is not the picture: the two control rows, the
// flex gaps between the three children, the container's own padding, and the
// stage margins. contentHeight() adds the picture to this.
const CONTROLS_H = 64 + STAGE_GAP * 2;

// Aspect presets for the dropdown, as written.
const PRESETS = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"];
// Two ratios within this of each other are the same preset, so 32:18 still
// reads as 16:9 rather than dropping the menu to Custom.
const RATIO_EPS = 1e-4;
const COLD_ASPECT = 16 / 9;

const COLD_MSG = "Run workflow to generate preview";

// Photoshop's cursors, by drag mode.
const CURSORS = {
    nw: "nwse-resize", se: "nwse-resize",
    ne: "nesw-resize", sw: "nesw-resize",
    n: "ns-resize", s: "ns-resize",
    w: "ew-resize", e: "ew-resize",
    move: "move",
};

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const svgEl = (tag) => document.createElementNS(SVG_NS, tag);

// The readout pill and the Reset button borrow the shapes already used by the
// ExtractFrame scrubber, so the two nodes read as one family. A stylesheet
// rather than inline styles because of :hover -- and because the overlay's
// stroke weights belong in CSS now that it is real geometry.
(function ensureCropStyle() {
    if (document.getElementById("kd_crop_style")) return;
    const style = document.createElement("style");
    style.id = "kd_crop_style";
    style.textContent =
        // touch-action:none so a drag on a trackpad/tablet doesn't turn into a
        // browser pan halfway through.
        ".kd-cr-stage{position:relative;width:100%;overflow:hidden;" +
        "border-radius:4px;background:#1a1a1a;touch-action:none;" +
        // On top of the container's flex gap: the picture wants more air
        // around it than the two control rows want between themselves.
        "margin:" + STAGE_GAP + "px 0;" +
        "aspect-ratio:16/9;}" +
        // object-fit:fill, with the stage locked to the source aspect ratio:
        // the picture and the overlay's viewBox then map onto each other
        // exactly, so a crop edge lands on the pixel it says it does.
        ".kd-cr-img{width:100%;height:100%;display:block;object-fit:fill;" +
        "user-select:none;-webkit-user-drag:none;pointer-events:none;}" +
        ".kd-cr-ov{position:absolute;inset:0;width:100%;height:100%;" +
        "display:block;pointer-events:none;}" +
        // Stroke widths are in css px of the element, not in the viewBox's
        // source pixels -- without this a stroke-width of 1 would mean one
        // pixel of a 4K plate and vanish.
        ".kd-cr-ov *{vector-effect:non-scaling-stroke;}" +
        ".kd-cr-ov .kd-scrim{fill:rgba(0,0,0,0.55);stroke:none;}" +
        ".kd-cr-ov .kd-grid{fill:none;stroke:rgba(255,255,255,0.30);" +
        "stroke-width:1;}" +
        ".kd-cr-ov .kd-box{fill:none;stroke:rgba(255,255,255,0.9);" +
        "stroke-width:1;}" +
        ".kd-cr-ov .kd-grip{fill:none;stroke:rgba(255,255,255,0.95);" +
        "stroke-width:3;stroke-linecap:butt;}" +
        // Ratio bar: preset menu, W, swap, H, Clear -- Photoshop's order.
        ".kd-cr-ratio{display:flex;align-items:center;gap:4px;flex:0 0 auto;" +
        "height:" + RATIO_H + "px;font-size:11px;}" +
        // color-scheme so the native <select> popup comes up dark too.
        ".kd-cr-sel{flex:0 0 auto;width:78px;height:22px;color-scheme:dark;" +
        "border:1px solid #555;background:#333;color:#ddd;border-radius:4px;" +
        "font-size:11px;padding:0 2px;cursor:pointer;}" +
        ".kd-cr-num{flex:1 1 0;min-width:24px;height:22px;box-sizing:border-box;" +
        "border:1px solid #555;background:#333;color:#ddd;border-radius:4px;" +
        "font-size:11px;padding:0 6px;font-variant-numeric:tabular-nums;}" +
        ".kd-cr-num:focus{outline:none;border-color:#888;}" +
        ".kd-cr-num::placeholder{color:#666;}" +
        ".kd-cr-swap{flex:0 0 auto;width:22px;height:22px;display:inline-flex;" +
        "align-items:center;justify-content:center;padding:0;line-height:0;" +
        "cursor:pointer;border:none;background:none;color:#aaa;" +
        "border-radius:4px;}" +
        ".kd-cr-swap:hover{color:#fff;background:#3a3a3a;}" +
        ".kd-cr-ratio .kd-cr-btn{height:22px;padding:0 8px;margin-left:0;}" +
        ".kd-cr-info{display:flex;align-items:center;gap:8px;flex:0 0 auto;" +
        "overflow:hidden;font-size:11px;color:#999;user-select:none;" +
        "white-space:nowrap;font-variant-numeric:tabular-nums;}" +
        ".kd-cr-pill{flex:0 1 auto;overflow:hidden;text-overflow:ellipsis;" +
        "padding:3px 8px;border:1px solid #555;background:#333;" +
        "border-radius:4px;}" +
        // No preview yet: drop the pill and let the message use the whole row.
        ".kd-cr-pill.kd-cr-cold{border:none;background:none;padding:0;}" +
        ".kd-cr-btn{flex:0 0 auto;margin-left:auto;padding:3px 8px;" +
        "cursor:pointer;border:1px solid #555;background:#333;color:#ddd;" +
        "border-radius:4px;font-size:11px;}" +
        ".kd-cr-btn:hover:not(:disabled){background:#404040;border-color:#777;" +
        "color:#fff;}" +
        ".kd-cr-btn:disabled{cursor:default;color:#666;border-color:#3a3a3a;" +
        "background:#2b2b2b;}";
    document.head.appendChild(style);
})();

app.registerExtension({
    name: "KD_Nodes.CropImage",

    async nodeCreated(node) {
        if (node.comfyClass !== "CropImageKD") return;

        const wx = node.widgets?.find((w) => w.name === "x");
        const wy = node.widgets?.find((w) => w.name === "y");
        const ww = node.widgets?.find((w) => w.name === "width");
        const wh = node.widgets?.find((w) => w.name === "height");
        if (!wx || !wy || !ww || !wh) return;

        // --- state ---
        // srcW/srcH are the full-resolution dimensions the widgets are
        // expressed in -- never the proxy's. They are also the overlay's
        // coordinate system, so a crop rect needs no conversion to be drawn.
        let srcW = 0, srcH = 0;
        let aspect = COLD_ASPECT;
        let hasImage = false;
        let rect = { x: 0, y: 0, w: 0, h: 0 };   // source px
        let drag = null;
        let syncing = false;         // guards the widget <-> box round trip

        // ---------------------------------------------------------------
        //  DOM
        // ---------------------------------------------------------------

        const container = document.createElement("div");
        container.style.cssText =
            "display:flex;flex-direction:column;gap:4px;width:100%;" +
            "box-sizing:border-box;padding:2px 0;";

        const stage = document.createElement("div");
        stage.className = "kd-cr-stage";

        const imgEl = document.createElement("img");
        imgEl.className = "kd-cr-img";
        imgEl.draggable = false;
        imgEl.style.visibility = "hidden";

        const svg = svgEl("svg");
        svg.setAttribute("class", "kd-cr-ov");
        // The stage is locked to the source aspect ratio, so stretching the
        // viewBox to fill it is exact rather than approximate.
        svg.setAttribute("preserveAspectRatio", "none");
        svg.style.display = "none";

        const scrim = svgEl("path");
        scrim.setAttribute("class", "kd-scrim");
        // Outer rect and inner rect in one path: even-odd punches the crop out
        // of the scrim, so there is no seam where two fills would have met.
        scrim.setAttribute("fill-rule", "evenodd");

        const grid = svgEl("path");
        grid.setAttribute("class", "kd-grid");

        const boxEl = svgEl("rect");
        boxEl.setAttribute("class", "kd-box");

        const grips = svgEl("path");
        grips.setAttribute("class", "kd-grip");

        svg.append(scrim, grid, boxEl, grips);
        stage.append(imgEl, svg);

        // --- ratio bar ---------------------------------------------
        // Deliberately not ComfyUI widgets: a widget can be converted to an
        // input socket, and a wired socket would silently outrank whatever the
        // box on screen is showing. This is a UI constraint on the box, so it
        // lives only in the UI -- and in node.properties, so it survives a save.

        const ratioRow = document.createElement("div");
        ratioRow.className = "kd-cr-ratio";

        const sel = document.createElement("select");
        sel.className = "kd-cr-sel";
        sel.title = "Lock the crop box to an aspect ratio";
        for (const [value, label] of [["", "Free"], ...PRESETS.map((r) => [r, r])]) {
            const o = document.createElement("option");
            o.value = value;
            o.textContent = label;
            sel.append(o);
        }
        // Shown when the typed numbers match no preset. Disabled so it can be
        // selected from code but not chosen from the menu, where picking
        // "Custom" would have nothing to mean.
        const customOpt = document.createElement("option");
        customOpt.value = "custom";
        customOpt.textContent = "Custom";
        customOpt.disabled = true;
        sel.append(customOpt);

        const wIn = document.createElement("input");
        wIn.className = "kd-cr-num";
        wIn.type = "text";
        wIn.inputMode = "decimal";
        wIn.placeholder = "W";
        wIn.title = "Ratio width";

        const hIn = document.createElement("input");
        hIn.className = "kd-cr-num";
        hIn.type = "text";
        hIn.inputMode = "decimal";
        hIn.placeholder = "H";
        hIn.title = "Ratio height";

        const swapBtn = document.createElement("button");
        swapBtn.className = "kd-cr-swap";
        swapBtn.title = "Swap width and height";
        swapBtn.setAttribute("aria-label", "Swap width and height");
        swapBtn.innerHTML =
            '<svg width="13" height="13" viewBox="0 0 13 13" fill="none" ' +
            'aria-hidden="true"><path d="M1.5 4.5h9M8.5 2 11 4.5 8.5 7" ' +
            'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" ' +
            'stroke-linejoin="round"/><path d="M11.5 8.5h-9M4.5 6 2 8.5 4.5 11" ' +
            'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" ' +
            'stroke-linejoin="round"/></svg>';

        const clearBtn = document.createElement("button");
        clearBtn.className = "kd-cr-btn";
        clearBtn.textContent = "Clear";
        clearBtn.title = "Clear the ratio lock";

        ratioRow.append(sel, wIn, swapBtn, hIn, clearBtn);

        const infoRow = document.createElement("div");
        infoRow.className = "kd-cr-info";

        const pill = document.createElement("span");
        pill.className = "kd-cr-pill kd-cr-cold";
        pill.textContent = COLD_MSG;

        const resetBtn = document.createElement("button");
        resetBtn.className = "kd-cr-btn";
        resetBtn.textContent = "Reset";
        resetBtn.title = "Clear the ratio and crop the full image";
        resetBtn.disabled = true;

        infoRow.append(pill, resetBtn);
        container.append(ratioRow, stage, infoRow);

        // ---------------------------------------------------------------
        //  Geometry
        // ---------------------------------------------------------------

        // Source px per css px of the element. clientWidth is a layout size, so
        // the graph's zoom does not enter into it -- which is the point: handle
        // arms and grab bands stay a fixed size relative to the node, and scale
        // with it exactly like every other part of the node does.
        const srcPerCss = () =>
            srcW > 0 && stage.clientWidth > 0 ? srcW / stage.clientWidth : 1;

        // Pointer position in source pixels. The bounding rect is in screen px
        // and already carries whatever zoom the graph is at, so the ratio back
        // to the source is correct at any zoom with nothing to keep in sync.
        function toSource(e) {
            const r = stage.getBoundingClientRect();
            if (!r.width || !r.height) return null;
            return {
                x: ((e.clientX - r.left) / r.width) * srcW,
                y: ((e.clientY - r.top) / r.height) * srcH,
            };
        }

        // Single path to a new rect: clamp into the source, push to the
        // widgets, redraw. Everything that moves the box goes through here.
        function setRect(x, y, w, h) {
            if (srcW < 1 || srcH < 1) return;
            const minW = Math.min(MIN_CROP, srcW);
            const minH = Math.min(MIN_CROP, srcH);
            w = clamp(Math.round(w), minW, srcW);
            h = clamp(Math.round(h), minH, srcH);
            x = clamp(Math.round(x), 0, srcW - w);
            y = clamp(Math.round(y), 0, srcH - h);
            rect = { x, y, w, h };
            writeWidgets();
            updateReadout();
            drawOverlay();
        }

        function writeWidgets() {
            syncing = true;
            try {
                wx.value = rect.x;
                wy.value = rect.y;
                ww.value = rect.w;
                wh.value = rect.h;
            } finally {
                syncing = false;
            }
            node.graph?.setDirtyCanvas(true);
        }

        // Widget values -> box. A width or height of 0 means "to the far edge",
        // matching clamp_rect in crop_kd.py, which is what makes an untouched
        // node crop the whole image.
        //
        // `driver` names the widget the user actually edited, so a locked ratio
        // knows which axis to keep and which to recompute. Typing a height and
        // watching the height change would be no use to anyone.
        function readWidgets(driver) {
            if (srcW < 1 || srcH < 1) return;
            const x = clamp(Math.round(+wx.value || 0), 0, srcW - 1);
            const y = clamp(Math.round(+wy.value || 0), 0, srcH - 1);
            let w = Math.round(+ww.value || 0);
            let h = Math.round(+wh.value || 0);
            w = w > 0 ? w : srcW - x;
            h = h > 0 ? h : srcH - y;

            const r = lockedRatio();
            if (r) {
                if (driver === "height") w = h * r;
                else h = w / r;
            }
            setRect(x, y, w, h);
        }

        // ---------------------------------------------------------------
        //  Ratio lock
        // ---------------------------------------------------------------

        // The live constraint, or null for free. Read from the fields rather
        // than mirrored into a variable -- the fields are the only state, so
        // there is nothing that can fall out of step with them.
        function lockedRatio() {
            const a = parseFloat(wIn.value);
            const b = parseFloat(hIn.value);
            return a > 0 && b > 0 ? a / b : null;
        }

        // Largest box of ratio r that fits the frame, at least MIN_CROP on both
        // axes, centred on (cx, cy) and pushed inside the frame. `area` is the
        // area to aim for; pass the current box's to keep a reshape roughly the
        // size it was rather than snapping to the whole frame.
        function ratioBox(r, cx, cy, area) {
            const minW = Math.min(MIN_CROP, srcW);
            const minH = Math.min(MIN_CROP, srcH);
            let w = Math.sqrt(Math.max(1, area) * r);
            let h = w / r;
            // Up to the minimum first, then down to the frame: the frame wins
            // if a source is too small to hold a MIN_CROP box at this ratio.
            const up = Math.max(1, minW / w, minH / h);
            w *= up; h *= up;
            const down = Math.min(1, srcW / w, srcH / h);
            w *= down; h *= down;
            return { x: cx - w / 2, y: cy - h / 2, w, h };
        }

        // Reshape the current box onto a newly applied ratio, keeping its
        // centre and roughly its area.
        function reshapeToRatio() {
            const r = lockedRatio();
            if (!r || srcW < 1) return;
            const b = ratioBox(r, rect.x + rect.w / 2, rect.y + rect.h / 2,
                               rect.w * rect.h);
            setRect(b.x, b.y, b.w, b.h);
        }

        // Reset means back to the untouched image: the whole frame, and no
        // ratio. Leaving the lock on would only reset half of what is showing
        // and leave the box constrained to something you have to go and clear
        // separately -- which is what Clear is already for.
        function resetBox() {
            if (srcW < 1) return;
            wIn.value = "";
            hIn.value = "";
            ratioChanged(false);
            setRect(0, 0, srcW, srcH);
        }

        // Menu follows the fields, never the other way round.
        function syncSelect() {
            const r = lockedRatio();
            if (r === null) {
                sel.value = wIn.value || hIn.value ? "custom" : "";
                return;
            }
            const hit = PRESETS.find((p) => {
                const [a, b] = p.split(":").map(Number);
                return Math.abs(a / b - r) < RATIO_EPS;
            });
            sel.value = hit || "custom";
        }

        // Serialized by LiteGraph as part of node.properties, so the lock comes
        // back with the workflow.
        function persistRatio() {
            try {
                node.properties = node.properties || {};
                node.properties.kd_crop_ratio = [wIn.value, hIn.value];
            } catch (err) {}
        }

        // commit=true means the user finished (picked a preset, left the field,
        // hit Enter, swapped) and the box should move now. While they are still
        // mid-keystroke only the menu label follows along -- reshaping on every
        // character makes the box jump through "1:9" on the way to "16:9".
        function ratioChanged(commit) {
            syncSelect();
            persistRatio();
            if (commit) reshapeToRatio();
        }

        // ---------------------------------------------------------------
        //  Overlay
        // ---------------------------------------------------------------

        // The crop box as a stroke of the given width should be DRAWN.
        //
        // A stroke straddles its own path, so a box flush with the frame puts
        // half its width outside the viewBox, where the stage's overflow:hidden
        // eats it -- that edge then looks half weight and its handles look sawn
        // off. Pull it in by half its width, but only on a side that is
        // actually against the frame; anywhere else the box stays exactly on
        // its coordinate.
        //
        // Each stroked element gets its OWN inset rather than sharing the
        // widest one. Share it and the outline lands half a handle-width inside
        // the frame while the handles still reach it, which reads as the
        // handles protruding past the border with a gap behind them. Inset by
        // its own half-width and every stroke, thick or thin, finishes exactly
        // on the frame edge.
        //
        // The scrim keeps the true rect: it defines what is excluded, and it
        // has no width on a flush side anyway.
        function strokedBox(strokeW) {
            const inset = (strokeW / 2) * srcPerCss();
            const x0 = Math.max(rect.x, inset);
            const y0 = Math.max(rect.y, inset);
            // A tiny crop pinned to an edge would otherwise invert; keep it a
            // rectangle rather than letting a side go negative.
            const x1 = Math.max(Math.min(rect.x + rect.w, srcW - inset), x0);
            const y1 = Math.max(Math.min(rect.y + rect.h, srcH - inset), y0);
            return { x0, y0, x1, y1 };
        }

        // Corner brackets plus a bar at the middle of each edge, as one path.
        // Arms are clamped to half the box so a small crop still reads as a
        // rectangle with corners rather than as a solid white frame.
        function gripPath(x, y, w, h) {
            const u = srcPerCss();
            const ah = Math.min(HANDLE_L * u, w / 2);
            const av = Math.min(HANDLE_L * u, h / 2);
            const x1 = x + w, y1 = y + h;
            const mx = x + w / 2, my = y + h / 2;
            return [
                `M${x} ${y + av}V${y}H${x + ah}`,
                `M${x1 - ah} ${y}H${x1}V${y + av}`,
                `M${x1} ${y1 - av}V${y1}H${x1 - ah}`,
                `M${x + ah} ${y1}H${x}V${y1 - av}`,
                `M${mx - ah / 2} ${y}H${mx + ah / 2}`,
                `M${mx - ah / 2} ${y1}H${mx + ah / 2}`,
                `M${x} ${my - av / 2}V${my + av / 2}`,
                `M${x1} ${my - av / 2}V${my + av / 2}`,
            ].join("");
        }

        function drawOverlay() {
            if (!hasImage || srcW < 1 || srcH < 1) {
                svg.style.display = "none";
                return;
            }
            svg.style.display = "";
            svg.setAttribute("viewBox", `0 0 ${srcW} ${srcH}`);

            scrim.setAttribute(
                "d",
                `M0 0H${srcW}V${srcH}H0Z ` +
                `M${rect.x} ${rect.y}H${rect.x + rect.w}` +
                `V${rect.y + rect.h}H${rect.x}Z`
            );

            // The outline, and the thirds grid that terminates on it.
            const { x0, y0, x1, y1 } = strokedBox(OUTLINE_W);
            const w = x1 - x0, h = y1 - y0;

            // Thirds: two lines each way, light weight, purely visual.
            const g = [];
            for (let i = 1; i <= 2; i++) {
                g.push(`M${x0 + (w * i) / 3} ${y0}V${y1}`);
                g.push(`M${x0} ${y0 + (h * i) / 3}H${x1}`);
            }
            grid.setAttribute("d", g.join(""));

            boxEl.setAttribute("x", x0);
            boxEl.setAttribute("y", y0);
            boxEl.setAttribute("width", w);
            boxEl.setAttribute("height", h);

            // The handles, on their own inset. Away from an edge this is the
            // same rect as the outline and the two are concentric; against one,
            // both finish flush and the thicker handle simply covers the
            // outline's band instead of overhanging it.
            const k = strokedBox(GRIP_W);
            grips.setAttribute(
                "d", gripPath(k.x0, k.y0, k.x1 - k.x0, k.y1 - k.y0));
        }

        function updateReadout() {
            if (!hasImage || srcW < 1) {
                pill.classList.add("kd-cr-cold");
                pill.textContent = COLD_MSG;
                resetBtn.disabled = true;
                return;
            }
            pill.classList.remove("kd-cr-cold");
            resetBtn.disabled = false;
            pill.textContent = `${rect.w} × ${rect.h}  ·  ${rect.x}, ${rect.y}`;
        }

        // ---------------------------------------------------------------
        //  Hit testing and dragging
        // ---------------------------------------------------------------

        // Everything here is in source pixels, the same units the overlay is
        // drawn in and the same units toSource() hands back.
        function hitTest(px, py) {
            if (!hasImage || srcW < 1) return null;
            const x0 = rect.x, y0 = rect.y;
            const x1 = rect.x + rect.w, y1 = rect.y + rect.h;

            // Grab bands are a fixed size in css px -- so they feel the same
            // whatever the node's size -- then clamped to a third of the box,
            // or a small crop would be all handle with nothing left to grab.
            const u = srcPerCss();
            const cx = Math.min(HANDLE_HIT * u, Math.max(1, rect.w / 3));
            const cy = Math.min(HANDLE_HIT * u, Math.max(1, rect.h / 3));
            const ex = Math.min(EDGE_HIT * u, Math.max(1, rect.w / 3));
            const ey = Math.min(EDGE_HIT * u, Math.max(1, rect.h / 3));

            // A corner is also within the grab band of two edges, and a corner
            // is what the user means there -- so corners are tested first, and
            // with a slightly wider radius.
            const cL = Math.abs(px - x0) <= cx, cR = Math.abs(px - x1) <= cx;
            const cT = Math.abs(py - y0) <= cy, cB = Math.abs(py - y1) <= cy;
            if (px >= x0 - cx && px <= x1 + cx &&
                py >= y0 - cy && py <= y1 + cy) {
                if (cL && cT) return "nw";
                if (cR && cT) return "ne";
                if (cL && cB) return "sw";
                if (cR && cB) return "se";
            }

            const inX = px >= x0 - ex && px <= x1 + ex;
            const inY = py >= y0 - ey && py <= y1 + ey;
            if (inY) {
                if (Math.abs(px - x0) <= ex) return "w";
                if (Math.abs(px - x1) <= ex) return "e";
            }
            if (inX) {
                if (Math.abs(py - y0) <= ey) return "n";
                if (Math.abs(py - y1) <= ey) return "s";
            }
            if (px > x0 && px < x1 && py > y0 && py < y1) return "move";
            return null;
        }

        // Resizing while the ratio is locked.
        //
        // A corner keeps the opposite corner pinned, exactly as it does when
        // free. The moving corner cannot follow the pointer everywhere -- only
        // along the ratio's diagonal -- so it goes to the nearest point ON that
        // diagonal, which is the perpendicular projection of the pointer onto
        // it. That is what makes the box feel like it is tracking the cursor
        // instead of picking one axis and ignoring the other.
        //
        // An edge keeps its opposite edge pinned and drives its own axis; the
        // other axis then follows from the ratio, growing about the box's
        // centre so the box stays put on that axis instead of drifting.
        function applyRatioDrag(mode, start, dx, dy, r) {
            const minW = Math.min(MIN_CROP, srcW);
            const minH = Math.min(MIN_CROP, srcH);
            const EPS = 1e-6;

            // Scale a candidate size to respect the minimum, then the room
            // available. Room wins: the box may never leave the frame.
            //
            // Rounded here rather than left to setRect, because every caller
            // below derives its pinned edge by subtracting the size from the
            // anchor. Round size and position separately and the two disagree
            // by a pixel about half the time, which shows up as the anchored
            // corner twitching while you drag the opposite one.
            const fit = (w, h, roomW, roomH) => {
                const up = Math.max(1, minW / Math.max(w, EPS),
                                       minH / Math.max(h, EPS));
                w *= up; h *= up;
                const down = Math.min(1, roomW / Math.max(w, EPS),
                                         roomH / Math.max(h, EPS));
                return [
                    clamp(Math.round(w * down), 1, Math.floor(roomW)),
                    clamp(Math.round(h * down), 1, Math.floor(roomH)),
                ];
            };

            if (mode.length === 2) {              // corner
                const west = mode.includes("w"), north = mode.includes("n");
                const ax = west ? start.x + start.w : start.x;
                const ay = north ? start.y + start.h : start.y;
                const px = (west ? start.x : start.x + start.w) + dx;
                const py = (north ? start.y : start.y + start.h) + dy;

                // Perpendicular projection of (w, h) onto the line h = w / r.
                const t = (Math.abs(px - ax) * r + Math.abs(py - ay)) /
                          (r * r + 1);
                let [w, h] = fit(t * r, t, west ? ax : srcW - ax,
                                           north ? ay : srcH - ay);
                setRect(west ? ax - w : ax, north ? ay - h : ay, w, h);
                return;
            }

            if (mode === "w" || mode === "e") {   // vertical edge
                const west = mode === "w";
                const ax = west ? start.x + start.w : start.x;
                const px = (west ? start.x : start.x + start.w) + dx;
                let w = Math.abs(px - ax);
                let h;
                [w, h] = fit(w, w / r, west ? ax : srcW - ax, srcH);
                setRect(west ? ax - w : ax,
                        start.y + start.h / 2 - h / 2, w, h);
                return;
            }

            const north = mode === "n";           // horizontal edge
            const ay = north ? start.y + start.h : start.y;
            const py = (north ? start.y : start.y + start.h) + dy;
            let h = Math.abs(py - ay);
            let w;
            [w, h] = fit(h * r, h, srcW, north ? ay : srcH - ay);
            setRect(start.x + start.w / 2 - w / 2,
                    north ? ay - h : ay, w, h);
        }

        // Alt held: scale about the box's centre instead of about the opposite
        // edge, the way Photoshop does. Every handle collapses to the same
        // shape here -- work out the half-extents the pointer is asking for,
        // double them, and re-centre -- because with the centre pinned there is
        // no anchor left to distinguish a corner from an edge.
        function applyCentredDrag(mode, start, dx, dy, r) {
            const cx = start.x + start.w / 2;
            const cy = start.y + start.h / 2;
            // The largest box that still fits the frame while staying centred
            // here: the nearer frame edge is what limits it.
            const roomW = 2 * Math.min(cx, srcW - cx);
            const roomH = 2 * Math.min(cy, srcH - cy);
            const minW = Math.min(MIN_CROP, srcW);
            const minH = Math.min(MIN_CROP, srcH);
            const EPS = 1e-6;

            // Half-extents the pointer implies, on whichever axes this handle
            // drives. An axis the handle does not touch keeps the size it had.
            //
            // Measured with a sign and floored at zero, not as |distance|:
            // dragging an edge past the centre has to collapse the box onto the
            // minimum, and taking the magnitude would mirror it into a large one
            // instead -- the same reason the anchored paths clamp rather than
            // flip.
            let hw = start.w / 2, hh = start.h / 2;
            if (mode.includes("w")) hw = Math.max(0, cx - (start.x + dx));
            else if (mode.includes("e")) hw = Math.max(0, start.x + start.w + dx - cx);
            if (mode.includes("n")) hh = Math.max(0, cy - (start.y + dy));
            else if (mode.includes("s")) hh = Math.max(0, start.y + start.h + dy - cy);

            let w, h;
            if (!r) {
                w = clamp(2 * hw, minW, Math.max(minW, roomW));
                h = clamp(2 * hh, minH, Math.max(minH, roomH));
            } else {
                if (mode.length === 2) {
                    // Corner: same projection onto the ratio diagonal as the
                    // anchored case, just done in half-extents.
                    const t = (hw * r + hh) / (r * r + 1);
                    w = 2 * t * r; h = 2 * t;
                } else if (mode === "w" || mode === "e") {
                    w = 2 * hw; h = w / r;
                } else {
                    h = 2 * hh; w = h * r;
                }
                const up = Math.max(1, minW / Math.max(w, EPS),
                                       minH / Math.max(h, EPS));
                w *= up; h *= up;
                const down = Math.min(1, roomW / Math.max(w, EPS),
                                         roomH / Math.max(h, EPS));
                w *= down; h *= down;
            }

            // Settle on an integer size FIRST, with the minimum applied last,
            // then place it around the centre. setRect enforces MIN_CROP too,
            // and if it raised the size after the position was already fixed
            // the box would slide off centre by exactly that difference --
            // which is what a narrow frame plus a locked ratio produces.
            w = Math.max(minW, Math.round(w));
            h = Math.max(minH, Math.round(h));
            setRect(Math.round(cx - w / 2), Math.round(cy - h / 2), w, h);
        }

        // Deltas are measured from where the drag started, never accumulated
        // frame to frame -- accumulating would drift by the rounding error on
        // every pointermove. It also means a modifier can be pressed or
        // released mid-drag and the box simply re-derives from the same
        // numbers, with nothing to unwind.
        function applyDrag(mode, start, dx, dy, alt) {
            if (mode === "move") {
                setRect(start.x + dx, start.y + dy, start.w, start.h);
                return;
            }

            const r = lockedRatio();
            if (alt) {
                applyCentredDrag(mode, start, dx, dy, r);
                return;
            }
            if (r) {
                applyRatioDrag(mode, start, dx, dy, r);
                return;
            }

            // Each grabbed edge moves; the opposite edge is the anchor. The box
            // is clamped at MIN_CROP rather than flipped through itself -- a
            // flip is a surprise you have to undo, a clamp just stops.
            const minW = Math.min(MIN_CROP, srcW);
            const minH = Math.min(MIN_CROP, srcH);
            let left = start.x, top = start.y;
            let right = start.x + start.w, bottom = start.y + start.h;

            if (mode.includes("w")) left = clamp(start.x + dx, 0, right - minW);
            if (mode.includes("e")) right = clamp(right + dx, left + minW, srcW);
            if (mode.includes("n")) top = clamp(start.y + dy, 0, bottom - minH);
            if (mode.includes("s")) bottom = clamp(bottom + dy, top + minH, srcH);

            // Round the EDGES, not the size. An edge this drag never touched is
            // already an integer and rounds to itself, so the anchored side
            // stays exactly where it was; rounding the size instead lets it
            // drift a pixel as the numbers cross a half.
            left = Math.round(left); top = Math.round(top);
            right = Math.round(right); bottom = Math.round(bottom);
            setRect(left, top, right - left, bottom - top);
        }

        stage.addEventListener("pointerdown", (e) => {
            if (e.button !== 0) return;
            const p = toSource(e);
            if (!p) return;
            const mode = hitTest(p.x, p.y);
            if (!mode) return;
            e.preventDefault();
            // Keep it off the LiteGraph canvas underneath, or the drag also
            // drags the node.
            e.stopPropagation();
            try { stage.setPointerCapture(e.pointerId); } catch (err) {}
            // lastX/lastY are kept so an Alt press with the pointer standing
            // still can re-run the drag from where it already is.
            drag = { mode, px: p.x, py: p.y, start: { ...rect },
                     lastX: p.x, lastY: p.y, alt: e.altKey };
            stage.style.cursor = CURSORS[mode] || "default";
        });

        function runDrag() {
            if (!drag) return;
            applyDrag(drag.mode, drag.start, drag.lastX - drag.px,
                      drag.lastY - drag.py, drag.alt);
        }

        stage.addEventListener("pointermove", (e) => {
            const p = toSource(e);
            if (!p) return;
            if (!drag) {
                stage.style.cursor = CURSORS[hitTest(p.x, p.y)] || "default";
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            drag.lastX = p.x;
            drag.lastY = p.y;
            drag.alt = e.altKey;
            runDrag();
        });

        // Alt pressed or released without the pointer moving still has to take
        // effect -- in Photoshop the box snaps to centred the instant you press
        // it. preventDefault because a bare Alt keydown moves focus to the
        // window menu on Windows, which would drop the drag.
        function onModifier(e) {
            if (!drag || e.altKey === drag.alt) return;
            e.preventDefault();
            drag.alt = e.altKey;
            runDrag();
        }
        window.addEventListener("keydown", onModifier);
        window.addEventListener("keyup", onModifier);

        const endDrag = (e) => {
            if (!drag) return;
            drag = null;
            try { stage.releasePointerCapture(e.pointerId); } catch (err) {}
            const p = toSource(e);
            stage.style.cursor = p
                ? CURSORS[hitTest(p.x, p.y)] || "default"
                : "default";
        };
        stage.addEventListener("pointerup", endDrag);
        stage.addEventListener("pointercancel", endDrag);
        stage.addEventListener("pointerleave", () => {
            if (!drag) stage.style.cursor = "default";
        });

        resetBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            resetBox();
        });

        // --- ratio bar wiring ---------------------------------------
        // Keystrokes are kept off the graph: ComfyUI binds single keys, and
        // Delete while a field has focus would otherwise delete the node.
        for (const el of [sel, wIn, hIn, swapBtn, clearBtn]) {
            el.addEventListener("pointerdown", (e) => e.stopPropagation());
            el.addEventListener("keydown", (e) => e.stopPropagation());
        }

        sel.addEventListener("change", () => {
            const v = sel.value;
            if (v === "custom") return;           // not user-selectable
            if (!v) {
                wIn.value = "";
                hIn.value = "";
            } else {
                const [a, b] = v.split(":");
                wIn.value = a;
                hIn.value = b;
            }
            ratioChanged(true);
        });

        for (const el of [wIn, hIn]) {
            // Mid-typing: menu label only.
            el.addEventListener("input", () => ratioChanged(false));
            // Blur or Enter: commit, and move the box.
            el.addEventListener("change", () => ratioChanged(true));
        }

        swapBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const t = wIn.value;
            wIn.value = hIn.value;
            hIn.value = t;
            ratioChanged(true);
        });

        // Clear only lets the box go -- it does not move it. Photoshop's does
        // the same, and re-cropping after unlocking is the normal next step.
        clearBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            wIn.value = "";
            hIn.value = "";
            ratioChanged(false);
        });

        // ---------------------------------------------------------------
        //  Widget wiring
        // ---------------------------------------------------------------

        for (const w of [wx, wy, ww, wh]) {
            const orig = w.callback;
            const driver = w.name;
            w.callback = function (value) {
                if (orig) orig.call(this, value);
                if (!syncing) readWidgets(driver);
            };
        }

        // ---------------------------------------------------------------
        //  Widget layout
        // ---------------------------------------------------------------

        // Height this widget needs at the node's current width. The image is
        // width-bound: as wide as the node allows, as tall as the aspect ratio
        // then requires. Reporting that as the minimum is what stops dragging
        // the bottom edge up from squashing the picture.
        const contentHeight = () =>
            Math.round(Math.max(64, node.size[0] - 20) / aspect) + CONTROLS_H;

        const domWidget = node.addDOMWidget("kd_crop_ui", "div", container, {
            serialize: false,
            getMinHeight: contentHeight,
        });

        // Fixed height, not "fill what's left" -- otherwise this widget absorbs
        // all the spare node height and any extra room opens up between the
        // numbers and the image instead of below everything.
        domWidget.computeSize = function (width) {
            return [width, contentHeight()];
        };

        // Image at the bottom: the four numbers first, then the picture.
        // Safe for execution -- ComfyUI sends widget values to Python keyed by
        // name, not by position, so reordering cannot misalign the inputs.
        const rest = node.widgets.filter((w) => w !== domWidget);
        node.widgets.length = 0;
        node.widgets.push(...rest, domWidget);
        insertGapAfter(node, "height");

        // ---------------------------------------------------------------
        //  Preview source
        // ---------------------------------------------------------------

        function applyPreview(p) {
            if (!p || !p.filename) return;
            srcW = parseInt(p.width) || 0;
            srcH = parseInt(p.height) || 0;
            if (srcW < 1 || srcH < 1) return;

            // The source ratio, not the proxy's: the overlay's viewBox is in
            // source pixels, so this is the ratio the stage has to hold for the
            // two to line up.
            aspect = srcW / srcH;
            stage.style.aspectRatio = `${srcW} / ${srcH}`;

            imgEl.onload = () => {
                hasImage = true;
                imgEl.style.visibility = "";
                // Adopt whatever the widgets currently say, clamped to the new
                // source. A fresh node reads 0/0 for width/height, which
                // readWidgets expands to the full image -- so the box starts
                // out around everything, like opening the crop tool.
                readWidgets();
                updateReadout();
                drawOverlay();
                // Grow the node if it is now too short, but never shrink it:
                // someone who dragged it taller keeps the extra room.
                const need = node.computeSize()[1];
                if (node.size[1] < need) node.setSize([node.size[0], need]);
                node.graph?.setDirtyCanvas(true);
            };
            imgEl.onerror = () => {
                // Most likely a restored URL pointing at a temp file that was
                // cleared on ComfyUI restart.
                hasImage = false;
                imgEl.style.visibility = "hidden";
                updateReadout();
                drawOverlay();
            };
            imgEl.src = api.apiURL(
                "/view?" + new URLSearchParams({
                    filename: p.filename,
                    subfolder: p.subfolder || "",
                    type: p.type || "temp",
                    t: Date.now(),
                })
            );
        }

        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            if (origOnExecuted) origOnExecuted.apply(this, arguments);
            const p = message?.kd_crop?.[0];
            if (!p) return;
            // Stashed on the node so a page refresh can restore the preview.
            // Serialized by LiteGraph as part of node.properties.
            try {
                node.properties = node.properties || {};
                node.properties.kd_crop_preview = p;
            } catch (err) {}
            applyPreview(p);
        };

        // ---------------------------------------------------------------
        //  Lifecycle
        // ---------------------------------------------------------------

        // Widget values aren't populated yet when onConfigure fires, so defer.
        const origOnConfigure = node.onConfigure;
        node.onConfigure = function () {
            if (origOnConfigure) origOnConfigure.apply(this, arguments);
            requestAnimationFrame(() => {
                const saved = node.properties?.kd_crop_ratio;
                if (Array.isArray(saved)) {
                    wIn.value = saved[0] ?? "";
                    hIn.value = saved[1] ?? "";
                    // No reshape: the four widgets already hold the box this
                    // workflow was saved with, and it was on-ratio then.
                    syncSelect();
                }
                const p = node.properties?.kd_crop_preview;
                if (p) applyPreview(p);
            });
        };

        // Only the node being resized matters here: it changes how many source
        // pixels a css pixel is worth, which is what sizes the handle arms.
        // Zooming the graph does not -- the browser rescales the <img> and the
        // SVG on its own, and clientWidth is unmoved by a css transform.
        const ro = new ResizeObserver(() => drawOverlay());
        ro.observe(stage);

        const origOnRemoved = node.onRemoved;
        node.onRemoved = function () {
            window.removeEventListener("keydown", onModifier);
            window.removeEventListener("keyup", onModifier);
            ro.disconnect();
            imgEl.onload = imgEl.onerror = null;
            imgEl.removeAttribute("src");
            if (origOnRemoved) origOnRemoved.apply(this, arguments);
        };

        updateReadout();

        // Sensible starting footprint so the image is usefully large.
        node.size[0] = Math.max(node.size[0], 320);
        node.size[1] = Math.max(node.size[1] || 0, 420);
    },
});
