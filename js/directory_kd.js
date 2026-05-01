import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "Comfy.DirectoryKD",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "DirectoryKD") return;

        const _onCreated = nodeType.prototype.onNodeCreated;

        nodeType.prototype.onNodeCreated = function () {
            _onCreated?.apply(this, arguments);

            this.addWidget("button", "Browse", null, async () => {
                const pathWidget = this.widgets.find(w => w.name === "path");

                try {
                    const res = await fetch("/directory_kd/open");
                    const data = await res.json();

                    if (!res.ok) {
                        alert(`Directory KD error:\n${data.error}`);
                        return;
                    }

                    if (data.path && pathWidget) {
                        pathWidget.value = data.path;
                        app.graph.setDirtyCanvas(true);
                    }
                } catch (err) {
                    alert(`Could not open directory picker:\n${err.message}`);
                }
            });

            const pathWidget = this.widgets.find(w => w.name === "path");
            if (pathWidget) {
                pathWidget.computedHeight = 60;
            }

            this.setSize([this.size[0], this.computeSize()[1] + 10]);
            app.graph.setDirtyCanvas(true);
        };
    },
});