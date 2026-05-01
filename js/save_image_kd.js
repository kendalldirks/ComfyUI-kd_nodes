import { app } from "../../scripts/app.js";

function insertGapAfter(node, widgetName) {
    const idx = node.widgets?.findIndex(w => w.name === widgetName);
    if (idx == null || idx === -1) return;
    node.widgets.splice(idx + 1, 0, {
        name: `_gap_after_${widgetName}`,
        type: "null",
        draw() {},
        computeSize: () => [0, 8],
        serializeValue: () => undefined,
    });
}

function linkBoolToDisable(node, boolWidgetName, targetNames) {
    const boolWidget = node.widgets?.find(w => w.name === boolWidgetName);
    const targets    = targetNames.map(n => node.widgets?.find(w => w.name === n)).filter(Boolean);
    if (!boolWidget || targets.length === 0) return;

    const update = (enabled) => {
        for (const w of targets) w.disabled = !enabled;
    };

    update(boolWidget.value);

    const origConfigure = node.onConfigure;
    node.onConfigure = function (config) {
        origConfigure?.call(this, config);
        update(boolWidget.value);
    };

    const origCallback = boolWidget.callback;
    boolWidget.callback = function (value) {
        update(value);
        origCallback?.call(this, value);
    };
}

app.registerExtension({
    name: "ComfyUI-kd_nodes.nodes",

    nodeCreated(node) {

        // ── SaveImageKD ─────────────────────────────────────────────
        if (node.comfyClass === "SaveImageKD") {
            insertGapAfter(node, "zero_padding");
            insertGapAfter(node, "auto_version_subfolder");
            linkBoolToDisable(node, "create_subfolder", [
                "subfolder_name",
                "auto_version_subfolder",
            ]);
            node.setSize(node.computeSize());
        }
    },
});