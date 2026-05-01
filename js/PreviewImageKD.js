import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "Comfy.PreviewImageKD",

    async nodeCreated(node) {
        if (node.comfyClass !== "PreviewImageKD") return;

        const origOnExecuted = node.onExecuted?.bind(node);
        node.onExecuted = function(output) {
            origOnExecuted?.(output);

            // Only add the button once
            if (node.widgets?.some(w => w.name === "Copy to Clipboard")) return;

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