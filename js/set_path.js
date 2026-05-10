import { app } from "../../../scripts/app.js";

function insertGapAfter(node, widgetName, height = 8) {
    const idx = node.widgets?.findIndex(w => w.name === widgetName);
    if (idx == null || idx === -1) return;
    node.widgets.splice(idx + 1, 0, {
        name: `_gap_after_${widgetName}`,
        type: "null",
        draw() {},
        computeSize: () => [0, height],
        serializeValue: () => undefined,
    });
}

app.registerExtension({
    name: "Comfy.SetPath",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "SetPath") return;

        const _onCreated = nodeType.prototype.onNodeCreated;

        nodeType.prototype.onNodeCreated = function () {
            _onCreated?.apply(this, arguments);

            let isBrowsing = false;

            this.addWidget("button", "Browse", null, async () => {
                if (isBrowsing) return;
                isBrowsing = true;
                const pathWidget = this.widgets.find(w => w.name === "path");

                try {
                    const res = await fetch("/set_path/open");
                    const data = await res.json();

                    if (!res.ok) {
                        alert(`Set Path error:\n${data.error}`);
                        return;
                    }

                    if (data.path && pathWidget) {
                        pathWidget.value = data.path;
                        app.graph.setDirtyCanvas(true);
                    }
                } catch (err) {
                    alert(`Could not open directory picker:\n${err.message}`);
                } finally {
                    isBrowsing = false;
                }
            });

            insertGapAfter(this, "Browse", 4);

            const pathWidget = this.widgets.find(w => w.name === "path");
            if (pathWidget) {
                pathWidget.computedHeight = 60;
            }

            this.setSize([this.size[0], this.computeSize()[1]]);
            app.graph.setDirtyCanvas(true);
        };
    },
});