import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "Comfy.PreviewImageKD",

    async nodeCreated(node) {
        if (node.comfyClass !== "PreviewImageKD") return;

        const origOnExecuted = node.onExecuted?.bind(node);
        node.onExecuted = function(output) {
            origOnExecuted?.(output);

            setTimeout(() => {
                // Remove existing button and gap if present
                ["Copy to Clipboard", "_gap_after_Copy to Clipboard"].forEach(name => {
                    const idx = node.widgets?.findIndex(w => w.name === name);
                    if (idx !== -1) node.widgets.splice(idx, 1);
                });

                const btn = node.addWidget("button", "Copy to Clipboard", null, async () => {
                    try {
                        const response = await fetch(node.imgs[0].src);
                        if (!response.ok) throw new Error(`HTTP ${response.status}`);
                        const blob = await response.blob();
                        const pngBlob = blob.type === "image/png" ? blob : await convertToPng(blob);
                        await navigator.clipboard.write([
                            new ClipboardItem({ "image/png": pngBlob })
                        ]);
                    } catch (err) {
                        console.error("[PreviewImageKD] Copy failed:", err);
                        alert("Copy failed: " + err.message);
                    }
                });
                btn.serialize = false;

                node.widgets.push({
                    name: "_gap_after_Copy to Clipboard",
                    type: "null",
                    draw() {},
                    computeSize: () => [0, 4],
                    serializeValue: () => undefined,
                });

                app.graph.setDirtyCanvas(true);
            }, 500);
        };
    },
});

async function convertToPng(blob) {
    const bitmap = await createImageBitmap(blob);
    const canvas = Object.assign(document.createElement("canvas"), {
        width: bitmap.width,
        height: bitmap.height,
    });
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}