import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { makeButton, addButtonRow } from "./kd_ui.js";

// Browse button for LoadImagesPathKD.
//
// Reuses /set_path/open rather than LoadVideoKD's /kd_nodes/open_video: this
// node's input is a `directory` it runs os.path.isdir() against, so a file
// picker would hand it a value that can never validate.

app.registerExtension({
    name: "Comfy.LoadImagesPathKD",

    async nodeCreated(node) {
        if (node.comfyClass !== "LoadImagesPathKD") return;

        const pathWidget =
            node.widgets?.find((w) => w.name === "directory") ||
            node.widgets?.find((w) => w.name === "path");
        if (!pathWidget) return;

        let isBrowsing = false;
        const browseBtn = makeButton("Browse");

        browseBtn.addEventListener("click", async () => {
            if (isBrowsing) return;
            isBrowsing = true;
            app.canvas.node_widget = null;
            try {
                const res = await api.fetchApi("/set_path/open");
                const data = await res.json();

                if (!res.ok) {
                    alert("Browse error:\n" + data.error);
                    return;
                }

                if (data.path) {
                    pathWidget.value = data.path;
                    pathWidget.callback?.(data.path);
                    app.graph.setDirtyCanvas(true);
                }
            } catch (err) {
                alert("Could not open folder picker:\n" + err.message);
            } finally {
                isBrowsing = false;
            }
        });

        addButtonRow(node, "kd_lip_browse", [browseBtn]);
    },
});
