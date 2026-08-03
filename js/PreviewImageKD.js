import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { makeButton, setEnabled, flash, addButtonRow } from "./kd_ui.js";

// Copy / Save buttons for PreviewImageKD.
//
// The image preview is ComfyUI's own — painted onto the LiteGraph canvas below
// the widgets — so the thumbnail grid, click-to-fullscreen and Open in
// MaskEditor all work as normal. The cost is that widgets can only sit above
// it; the native preview isn't a widget, so it can't be reordered among them.
// (A version that renders the image in a DOM widget, to put the widgets
// underneath, is parked in PreviewImageKD.gridview.js.bak.)
//
// Save exists because the clipboard can't carry the workflow: browsers decode
// and re-encode images written through the Async Clipboard API, which strips
// the PNG text chunks. Downloading the original file keeps prompt and workflow
// intact, so the saved image can be dragged back into ComfyUI.

// Image list straight from the executed payload.
//
// Deliberately not node.imgs: ComfyUI loads those <img> objects asynchronously
// and assigns the array afterwards, so anything reading it right after
// execution sees the previous state. app.nodeOutputs is written synchronously
// when the message arrives, and survives across redraws.
function imageList(node) {
    const out = app.nodeOutputs?.[node.id];
    return Array.isArray(out?.images) ? out.images : [];
}

// imageIndex tracks whichever image of a batch ComfyUI is currently showing.
function viewParams(node) {
    const list = imageList(node);
    const p = list[node.imageIndex ?? 0] || list[0];
    if (!p?.filename) return null;
    const meta = {
        filename: p.filename,
        subfolder: p.subfolder || "",
        type: p.type || "temp",
    };
    return { ...meta, src: api.apiURL("/view?" + new URLSearchParams(meta)) };
}

async function downloadInBrowser(src, filename) {
    // Fetching the original bytes and handing them to a download link keeps the
    // file byte-identical — metadata and all.
    const resp = await fetch(src);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const url = URL.createObjectURL(await resp.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "preview.png";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function toPngBlob(blob) {
    if (blob.type === "image/png") return blob;
    const bitmap = await createImageBitmap(blob);
    const canvas = Object.assign(document.createElement("canvas"), {
        width: bitmap.width,
        height: bitmap.height,
    });
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

app.registerExtension({
    name: "Comfy.PreviewImageKD",

    async nodeCreated(node) {
        if (node.comfyClass !== "PreviewImageKD") return;

        const copyBtn = makeButton("Copy to Clipboard");
        const saveBtn = makeButton("Save");

        addButtonRow(node, "kd_pi_buttons", [copyBtn, saveBtn]);

        // Greyed out until there is something to act on. The guard means the
        // DOM is only touched when the state actually flips.
        let hadImages = null;
        function syncEnabled() {
            const has = imageList(node).length > 0;
            if (has === hadImages) return;
            hadImages = has;
            setEnabled(copyBtn, has);
            setEnabled(saveBtn, has);
        }
        syncEnabled();

        // app.nodeOutputs is already populated by the time onExecuted runs, so
        // this needs no delay and no polling.
        const origOnExecuted = node.onExecuted;
        node.onExecuted = function () {
            origOnExecuted?.apply(this, arguments);
            syncEnabled();
        };

        // Loading a workflow restores outputs without firing onExecuted.
        const origOnConfigure = node.onConfigure;
        node.onConfigure = function () {
            origOnConfigure?.apply(this, arguments);
            requestAnimationFrame(syncEnabled);
        };

        copyBtn.addEventListener("click", async () => {
            const info = viewParams(node);
            if (!info) return;
            try {
                const resp = await fetch(info.src);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const png = await toPngBlob(await resp.blob());
                await navigator.clipboard.write([
                    new ClipboardItem({ "image/png": png }),
                ]);
                flash(copyBtn, "Copied");
            } catch (err) {
                console.error("[PreviewImageKD] copy failed:", err);
                flash(copyBtn, "Copy failed");
            }
        });

        saveBtn.addEventListener("click", async () => {
            const info = viewParams(node);
            if (!info) return;

            const dest =
                (node.widgets?.find((w) => w.name === "save_path")?.value || "").trim();

            // Given a path, try to write it server-side. Any failure — blank,
            // unwritable, wrong drive — quietly becomes a browser download.
            if (dest) {
                try {
                    const resp = await api.fetchApi("/kd_nodes/save_preview", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            filename: info.filename,
                            subfolder: info.subfolder,
                            type: info.type,
                            path: dest,
                        }),
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        console.log("[PreviewImageKD] saved to", data.saved);
                        return flash(saveBtn, "Saved");
                    }
                    const detail = await resp.json().catch(() => ({}));
                    console.warn("[PreviewImageKD] path unusable, downloading instead:",
                                 detail.error);
                } catch (err) {
                    console.warn("[PreviewImageKD] save route failed, downloading instead:",
                                 err);
                }
            }

            try {
                await downloadInBrowser(info.src, info.filename);
                flash(saveBtn, dest ? "Downloaded" : "Saved");
            } catch (err) {
                console.error("[PreviewImageKD] download failed:", err);
                flash(saveBtn, "Save failed");
            }
        });
    },
});
