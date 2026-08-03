import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { insertGapAfter } from "./save_image_kd.js";

// Scrubber UI for ExtractFrameKD.
//
// The node encodes the incoming IMAGE batch to a small all-intra mp4 in temp on
// execution and hands us its /view coordinates. Everything here is client-side
// from that point: seeking and the frame/timecode readout. Scrubbing only moves
// the `frame` widget — ComfyUI has no reactive execution, so nothing runs until
// the next queue.

const CHEVRON = (d) =>
    '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">' +
    `<path d="${d}" stroke="currentColor" stroke-width="1.6" ` +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

const CHEVRON_LEFT = CHEVRON("M7.5 2.5L4 6l3.5 3.5");
const CHEVRON_RIGHT = CHEVRON("M4.5 2.5L8 6l-3.5 3.5");

// Range inputs are styled through vendor pseudo-elements, which can't be set
// via element.style -- they need a real stylesheet.  Injected once, same
// approach as ensureKdPreviewStyle() in kd_video.js.
//
// The filled section left of the playhead is a linear-gradient sized by the
// --kd-p custom property (0..1) rather than ::-moz-range-progress, because the
// gradient renders identically in every engine while the native pseudo-element
// only exists in Firefox.
//
// The browser keeps the thumb inside the track, so its centre travels from
// half-a-thumb to width-minus-half-a-thumb, not 0..100%.  The fill has to use
// that same range or the two drift apart by 6px at each end.
(function ensureScrubStyle() {
    if (document.getElementById("kd_extract_frame_style")) return;
    const fillWidth = "calc(6px + (100% - 12px) * var(--kd-p, 0))";
    const track =
        "height:4px;border:none;border-radius:2px;" +
        `background:linear-gradient(#e8e8e8,#e8e8e8) 0/${fillWidth} 100% ` +
        "no-repeat,#4a4a4a;";
    // A gradient can't have rounded corners, so the bar is an SVG rect with rx.
    const playhead =
        "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'" +
        " width='3' height='12'%3E%3Crect width='3' height='12' rx='1.5'" +
        " fill='%23ffffff'/%3E%3C/svg%3E\")";
    // 12px wide for a usable grab target, but only a 3px bar is painted.
    const thumb =
        "width:12px;height:12px;border:none;border-radius:0;" +
        `background:${playhead} center/3px 12px no-repeat;`;
    const style = document.createElement("style");
    style.id = "kd_extract_frame_style";
    style.textContent =
        ".kd-ef-scrub{-webkit-appearance:none;appearance:none;width:100%;" +
        "background:transparent;margin:6px 0;cursor:pointer;}" +
        ".kd-ef-scrub:focus{outline:none;}" +
        `.kd-ef-scrub::-webkit-slider-runnable-track{${track}}` +
        `.kd-ef-scrub::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;` +
        `margin-top:-4px;${thumb}}` +
        `.kd-ef-scrub::-moz-range-track{${track}}` +
        `.kd-ef-scrub::-moz-range-thumb{${thumb}}` +
        // Timecode wears the outline; the arrows are bare glyphs.
        ".kd-ef-tc{justify-self:start;max-width:100%;overflow:hidden;" +
        "text-overflow:ellipsis;padding:3px 8px;border:1px solid #555;" +
        "background:#333;border-radius:4px;}" +
        // No preview yet: drop the pill and let the message use the whole row.
        ".kd-ef-tc.kd-ef-cold{grid-column:1/-1;justify-self:stretch;" +
        "border:none;background:none;padding:0;}" +
        ".kd-ef-btn{flex:0 0 auto;display:inline-flex;align-items:center;" +
        "justify-content:center;padding:3px 6px;cursor:pointer;line-height:0;" +
        "border:none;background:none;color:#ddd;border-radius:4px;}" +
        ".kd-ef-btn:hover{color:#fff;}";
    document.head.appendChild(style);
})();

// Height of everything under the video (slider + readout + gaps + padding).
// The preview is sized purely by the node's WIDTH, so the node's minimum
// height is whatever the video needs at that width, plus this.
const CONTROLS_H = 60;

const COLD_MSG = "Run workflow to generate preview";

function formatTimecode(frame, fps) {
    if (!(fps > 0)) return "--:--:--";
    const r = Math.max(1, Math.round(fps));
    const parts = [
        Math.floor(frame / fps / 60),
        Math.floor(frame / fps) % 60,
        frame % r,
    ];
    return parts.map((v) => String(Math.floor(v)).padStart(2, "0")).join(":");
}

app.registerExtension({
    name: "KD_Nodes.ExtractFrame",

    async nodeCreated(node) {
        if (node.comfyClass !== "ExtractFrameKD") return;

        const frameWidget = node.widgets.find((w) => w.name === "frame");
        const fpsWidget = node.widgets.find((w) => w.name === "fps");
        if (!frameWidget) return;

        // --- state ---
        // Two separate rates, deliberately.  mediaFps is what the loaded proxy
        // was actually encoded at and is the only thing seeking may use;
        // displayFps is the widget and only labels the timecode.  Sharing one
        // value meant editing the widget re-scaled seeks against a video that
        // hadn't changed, so the whole clip collapsed into the first slice of
        // the timeline.
        let totalFrames = 0;
        let aspect = 16 / 9;   // drives the width-bound sizing below
        let mediaFps = 24.0;
        let displayFps = fpsWidget?.value || 24.0;
        let pendingSeek = null;

        // ---------------------------------------------------------------
        //  Build the UI — one container becomes a single DOM widget
        // ---------------------------------------------------------------

        const container = document.createElement("div");
        container.style.cssText =
            "display:flex;flex-direction:column;gap:4px;width:100%;" +
            "box-sizing:border-box;padding:2px 0;";

        // Sized by width alone: the video fills the node's width and its height
        // follows from the aspect ratio. Extra vertical space simply sits below
        // the controls, and the node cannot be dragged shorter than the video
        // needs (see getMinHeight). The element box therefore always matches the
        // picture exactly, so the rounded corners hug the video.
        const videoBox = document.createElement("div");
        videoBox.style.cssText =
            "flex:0 0 auto;width:100%;display:flex;align-items:center;" +
            "justify-content:center;overflow:hidden;";

        const videoEl = document.createElement("video");
        videoEl.controls = false;
        videoEl.muted = true;
        videoEl.preload = "auto";
        videoEl.draggable = false;
        // width:100% + aspect-ratio means the height is derived, never capped
        // by the node. No max-height, so making the node shorter cannot squash
        // it -- getMinHeight below stops the node shrinking past what it needs.
        videoEl.style.cssText =
            "width:100%;height:auto;display:block;" +
            "background:transparent;border-radius:4px;aspect-ratio:16/9;";

        // Real ratio as soon as it's known: drives both the element box (so the
        // rounded corners hug the video) and the node's minimum height.
        videoEl.addEventListener("loadedmetadata", () => {
            if (!videoEl.videoWidth || !videoEl.videoHeight) return;
            aspect = videoEl.videoWidth / videoEl.videoHeight;
            videoEl.style.aspectRatio =
                `${videoEl.videoWidth} / ${videoEl.videoHeight}`;
            // Grow the node if it is now too short, but never shrink it --
            // a user who dragged it taller keeps the extra room.
            const need = node.computeSize()[1];
            if (node.size[1] < need) node.setSize([node.size[0], need]);
            node.graph?.setDirtyCanvas(true);
        });

        videoBox.appendChild(videoEl);

        // full-width scrub bar
        const slider = document.createElement("input");
        slider.type = "range";
        slider.className = "kd-ef-scrub";
        slider.min = "0";
        slider.max = "0";
        slider.value = "0";
        slider.style.cssText = "flex:0 0 auto;min-width:0;";

        // readout row: timecode left, [◀ 45 / 135 ▶] centred.
        // Grid rather than flex, so the middle group centres on the row itself
        // and doesn't drift sideways as the timecode text changes width.
        const infoRow = document.createElement("div");
        infoRow.style.cssText =
            "display:grid;grid-template-columns:1fr auto 1fr;align-items:center;" +
            "gap:8px;flex:0 0 auto;overflow:hidden;font-size:11px;color:#999;" +
            "user-select:none;white-space:nowrap;" +
            "font-variant-numeric:tabular-nums;";

        const tcLabel = document.createElement("span");
        tcLabel.className = "kd-ef-tc kd-ef-cold";
        tcLabel.textContent = COLD_MSG;

        const prevBtn = document.createElement("button");
        prevBtn.className = "kd-ef-btn";
        prevBtn.innerHTML = CHEVRON_LEFT;
        prevBtn.title = "Previous frame";
        prevBtn.setAttribute("aria-label", "Previous frame");

        const nextBtn = document.createElement("button");
        nextBtn.className = "kd-ef-btn";
        nextBtn.innerHTML = CHEVRON_RIGHT;
        nextBtn.title = "Next frame";
        nextBtn.setAttribute("aria-label", "Next frame");

        const frameLabel = document.createElement("span");
        frameLabel.style.cssText = "min-width:0;text-align:center;";

        // Hidden (not just invisible) until there's a preview, so the cold
        // message gets the full width of the row.
        const nudge = document.createElement("div");
        nudge.style.cssText = "display:none;align-items:center;gap:6px;";
        nudge.append(prevBtn, frameLabel, nextBtn);

        infoRow.append(tcLabel, nudge, document.createElement("span"));
        container.append(videoBox, slider, infoRow);

        // NOTE: deliberately no pointer-event forwarding to app.canvas here.
        // kd_video.js forwards everything so the node stays draggable through
        // the preview, but that also makes sliders and buttons dead. Drag this
        // node by its title bar instead.
        // Height this widget actually needs at the node's current width.
        const contentHeight = () =>
            Math.round(Math.max(64, node.size[0] - 20) / aspect) + CONTROLS_H;

        const domWidget = node.addDOMWidget("kd_ef_ui", "div", container, {
            serialize: false,
            // Width-bound: the video is as wide as the node allows and as tall
            // as its aspect ratio then requires. Reporting that as the minimum
            // is what stops dragging the bottom edge up from squashing it.
            getMinHeight: contentHeight,
        });

        // Fixed height, not "fill whatever is left". Without this the widget
        // absorbs all the spare node height and shoves the `frame` widget down
        // to the bottom edge; with it, `frame` sits directly under the readout
        // and any extra height falls below everything instead.
        domWidget.computeSize = function (width) {
            return [width, contentHeight()];
        };

        // Widget order: fps on top, then the player, then frame last.
        // Safe for execution — ComfyUI sends widget values to Python keyed by
        // name, not by position, so reordering can't misalign the inputs.
        const rest = node.widgets.filter(
            (w) => w !== domWidget && w !== frameWidget && w !== fpsWidget
        );
        node.widgets.length = 0;
        if (fpsWidget) node.widgets.push(fpsWidget);
        node.widgets.push(domWidget, ...rest);
        node.widgets.push(frameWidget);

        // ---------------------------------------------------------------
        //  Seeking
        // ---------------------------------------------------------------

        // currentTime assignment is async. Dragging the slider fires `input`
        // faster than the decoder can settle, so hold at most one pending
        // target and apply it when the previous seek completes.
        function requestSeek(t) {
            if (videoEl.seeking) {
                pendingSeek = t;
                return;
            }
            videoEl.currentTime = t;
        }

        videoEl.addEventListener("seeked", () => {
            if (pendingSeek !== null) {
                const t = pendingSeek;
                pendingSeek = null;
                videoEl.currentTime = t;
            }
        });

        function updateLabel(v) {
            if (!totalFrames) {
                tcLabel.classList.add("kd-ef-cold");
                tcLabel.textContent = COLD_MSG;
                frameLabel.textContent = "";
                nudge.style.display = "none";
                return;
            }
            tcLabel.classList.remove("kd-ef-cold");
            nudge.style.display = "flex";
            tcLabel.textContent = formatTimecode(v, displayFps);
            frameLabel.textContent = `${v} / ${totalFrames}`;
        }

        // Single path to land on a frame: clamp, sync slider + widget + label, seek.
        // Seek target is plain v / mediaFps — verified exact at 24, 23.976,
        // 29.97, 30 and 8 fps against the all-intra encode. Any forward offset,
        // however small, overshoots by one frame. Do not "fix" this.
        // mediaFps, never displayFps: this must track the loaded video.
        function gotoFrame(v) {
            const maxIdx = totalFrames > 0 ? totalFrames - 1 : 0;
            v = Math.max(0, Math.min(Math.round(v), maxIdx));
            slider.value = String(v);
            frameWidget.value = v;
            // Drives the filled section of the track. Unitless 0..1 so the CSS
            // can multiply it against the thumb's real travel distance.
            slider.style.setProperty(
                "--kd-p", (maxIdx > 0 ? v / maxIdx : 0).toFixed(5));
            updateLabel(v);
            if (totalFrames > 0 && mediaFps > 0) requestSeek(v / mediaFps);
            return v;
        }

        // ---------------------------------------------------------------
        //  Event wiring
        // ---------------------------------------------------------------

        slider.addEventListener("input", () => {
            gotoFrame(parseInt(slider.value));
        });

        prevBtn.addEventListener("click", () => gotoFrame(parseInt(slider.value) - 1));
        nextBtn.addEventListener("click", () => gotoFrame(parseInt(slider.value) + 1));

        // `frame` widget typed or dragged → move the preview to match
        const origFrameCb = frameWidget.callback;
        frameWidget.callback = function (value) {
            if (origFrameCb) origFrameCb.call(this, value);
            gotoFrame(value);
        };

        // `fps` widget → relabel the timecode only. It must not touch mediaFps:
        // the loaded proxy's real rate is unchanged, so seeking must be too.
        if (fpsWidget) {
            const origFpsCb = fpsWidget.callback;
            fpsWidget.callback = function (value) {
                if (origFpsCb) origFpsCb.call(this, value);
                const v = parseFloat(value);
                if (v > 0) {
                    displayFps = v;
                    updateLabel(parseInt(slider.value));
                }
            };
        }

        videoEl.addEventListener("error", () => {
            // Most likely a restored URL pointing at a temp file that was
            // cleared on ComfyUI restart.
            totalFrames = 0;
            updateLabel(0);
        });

        // ---------------------------------------------------------------
        //  Preview source
        // ---------------------------------------------------------------

        function applyPreview(p) {
            if (!p || !p.filename) return;
            totalFrames = p.frames || 0;
            // The proxy's own encode rate. Never the widget — that only labels
            // the timecode and would desync seeking from the loaded video.
            mediaFps = p.fps > 0 ? p.fps : 24.0;

            const maxIdx = Math.max(0, totalFrames - 1);
            slider.max = String(maxIdx);
            if (frameWidget.options) frameWidget.options.max = maxIdx;

            videoEl.src = api.apiURL(
                "/view?" +
                    new URLSearchParams({
                        filename: p.filename,
                        subfolder: p.subfolder || "",
                        type: p.type || "temp",
                        t: Date.now(),
                    })
            );

            // Seek once the first frames are decodable, not before.
            videoEl.addEventListener(
                "loadeddata",
                () => gotoFrame(frameWidget.value),
                { once: true }
            );
            updateLabel(Math.min(frameWidget.value, maxIdx));
        }

        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            if (origOnExecuted) origOnExecuted.apply(this, arguments);
            const p = message?.kd_frame?.[0];
            if (!p) return;
            // Stashed on the node so a page refresh can restore the player.
            // Serialized by LiteGraph as part of node.properties.
            try {
                node.properties = node.properties || {};
                node.properties.kd_preview = p;
            } catch (e) {}
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
                if (fpsWidget?.value > 0) displayFps = fpsWidget.value;
                const p = node.properties?.kd_preview;
                if (p) applyPreview(p);
            });
        };

        const origOnRemoved = node.onRemoved;
        node.onRemoved = function () {
            videoEl.removeAttribute("src");
            videoEl.load();
            if (origOnRemoved) origOnRemoved.apply(this, arguments);
        };

        // A little breathing room between the last widget and the node edge.
        insertGapAfter(node, "frame");

        // Sensible starting footprint so the preview is usefully large
        node.size[0] = Math.max(node.size[0], 320);
        node.size[1] = Math.max(node.size[1] || 0, 400);
    },
});
