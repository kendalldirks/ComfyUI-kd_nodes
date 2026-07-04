import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'

function chainCallback(object, property, callback) {
    if (object == undefined) {
        console.error("Tried to add callback to non-existant object")
        return;
    }
    if (property in object && object[property]) {
        const callback_orig = object[property]
        object[property] = function () {
            const r = callback_orig.apply(this, arguments);
            return callback.apply(this, arguments) ?? r
        };
    } else {
        object[property] = callback;
    }
}

function fitHeight(node) {
    node.setSize([node.size[0], node.computeSize([node.size[0], node.size[1]])[1]])
    node?.graph?.setDirtyCanvas(true);
}

function addVideoPreview(nodeType) {
    chainCallback(nodeType.prototype, "onNodeCreated", function() {
        var element = document.createElement("div");
        const previewNode = this;
        var previewWidget = this.addDOMWidget("videopreview", "preview", element, {
            serialize: false,
            hideOnZoom: false,
            getValue() { return element.value; },
            setValue(v) { element.value = v; },
        });

        previewWidget.computeSize = function(width) {
            if (this.aspectRatio && !this.parentEl.hidden) {
                let height = (previewNode.size[0] - 20) / this.aspectRatio + 10;
                if (!(height > 0)) {
                    height = 0;
                }
                this.computedHeight = height + 10;
                return [width, height];
            }
            return [width, -4];
        }

        // Pass through mouse events to canvas
        element.addEventListener('contextmenu', (e) => {
            e.preventDefault()
            return app.canvas._mousedown_callback(e)
        }, true);
        element.addEventListener('pointerdown', (e) => {
            e.preventDefault()
            return app.canvas._mousedown_callback(e)
        }, true);
        element.addEventListener('mousewheel', (e) => {
            e.preventDefault()
            return app.canvas._mousewheel_callback(e)
        }, true);
        element.addEventListener('pointermove', (e) => {
            e.preventDefault()
            return app.canvas._mousemove_callback(e)
        }, true);
        element.addEventListener('pointerup', (e) => {
            e.preventDefault()
            return app.canvas._mouseup_callback(e)
        }, true);
        element.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            app.dragOverNode = this
        })

        previewWidget.value = {hidden: false, paused: false, params: {}, muted: false}
        previewWidget.parentEl = document.createElement("div");
        previewWidget.parentEl.className = "kd_video_preview";
        previewWidget.parentEl.style['width'] = "100%"
        element.appendChild(previewWidget.parentEl);

        previewWidget.videoEl = document.createElement("video");
        previewWidget.videoEl.controls = false;
        previewWidget.videoEl.loop = true;
        previewWidget.videoEl.muted = true;
        previewWidget.videoEl.style['width'] = "100%"
        previewWidget.videoEl.addEventListener("loadedmetadata", () => {
            previewWidget.aspectRatio = previewWidget.videoEl.videoWidth / previewWidget.videoEl.videoHeight;
            fitHeight(this);
        });
        previewWidget.videoEl.addEventListener("error", () => {
            previewWidget.parentEl.hidden = true;
            fitHeight(this);
        });
        previewWidget.videoEl.onmouseenter = () => {
            previewWidget.videoEl.muted = previewWidget.value.muted
        };
        previewWidget.videoEl.onmouseleave = () => {
            previewWidget.videoEl.muted = true;
        };

        previewWidget.imgEl = document.createElement("img");
        previewWidget.imgEl.style['width'] = "100%"
        previewWidget.imgEl.hidden = true;
        previewWidget.imgEl.onload = () => {
            previewWidget.aspectRatio = previewWidget.imgEl.naturalWidth / previewWidget.imgEl.naturalHeight;
            fitHeight(this);
        };

        previewWidget.parentEl.appendChild(previewWidget.videoEl)
        previewWidget.parentEl.appendChild(previewWidget.imgEl)

        var timeout = null;
        this.updateParameters = (params, force_update) => {
            if (!previewWidget.value.params) {
                if (typeof(previewWidget.value) != 'object') {
                    previewWidget.value = {hidden: false, paused: false}
                }
                previewWidget.value.params = {}
            }
            Object.assign(previewWidget.value.params, params)
            if (timeout) {
                clearTimeout(timeout);
            }
            if (force_update) {
                previewWidget.updateSource();
            } else {
                timeout = setTimeout(() => previewWidget.updateSource(), 100);
            }
        };

        previewWidget.updateSource = function () {
            if (this.value.params == undefined || !this.value.params.filename) {
                return;
            }
            let params = {}
            Object.assign(params, this.value.params);
            params.timestamp = Date.now()
            this.parentEl.hidden = this.value.hidden;

            if (params.format?.split('/')[0] == 'image') {
                let url = api.apiURL('/kd_nodes/view_video?' + new URLSearchParams({
                    filename: params.filename,
                    timestamp: params.timestamp
                }));
                this.imgEl.src = url;
                this.videoEl.hidden = true;
                this.imgEl.hidden = false;
            } else {
                // Calculate target width for preview scaling (same as VHS)
                let target_width = (previewNode.size[0] - 20) * 2 || 256;
                if (target_width < 256) {
                    target_width = 256;
                }
                params.force_size = target_width + "x?"

                this.videoEl.autoplay = !this.value.paused && !this.value.hidden;
                this.videoEl.src = api.apiURL('/kd_nodes/view_video?' + new URLSearchParams(params));
                this.videoEl.hidden = false;
                this.imgEl.hidden = true;
            }
        }
        previewWidget.callback = previewWidget.updateSource
    });
}

function addBrowseWidget(nodeType, widgetName) {
    chainCallback(nodeType.prototype, "onNodeCreated", function() {
        const pathWidget = this.widgets.find((w) => w.name === widgetName);
        let isBrowsing = false;

        let browseWidget = this.addWidget("button", "Browse", null, async () => {
            if (isBrowsing) return;
            isBrowsing = true;
            app.canvas.node_widget = null;
            try {
                const currentPath = pathWidget?.value || "";
                const params = new URLSearchParams({path: currentPath});
                const res = await fetch("/kd_nodes/open_video?" + params);
                const data = await res.json();

                if (!res.ok) {
                    alert("Browse error:\n" + data.error);
                    return;
                }

                if (data.path && pathWidget) {
                    pathWidget.value = data.path;
                    if (pathWidget.callback) {
                        pathWidget.callback(data.path);
                    }
                    app.graph.setDirtyCanvas(true);
                }
            } catch (err) {
                alert("Could not open video picker:\n" + err.message);
            } finally {
                isBrowsing = false;
            }
        });
        browseWidget.options.serialize = false;

        // Move browse button to just before the video preview widget
        setTimeout(() => {
            const browseIdx = this.widgets.findIndex((w) => w.name === "Browse");
            const previewIdx = this.widgets.findIndex((w) => w.name === "videopreview");
            if (browseIdx > -1 && previewIdx > -1 && browseIdx > previewIdx) {
                const [btn] = this.widgets.splice(browseIdx, 1);
                const newPreviewIdx = this.widgets.findIndex((w) => w.name === "videopreview");
                this.widgets.splice(newPreviewIdx, 0, btn);
            }
            this.setSize([this.size[0], this.computeSize([this.size[0], this.size[1]])[1]]);
            app.graph.setDirtyCanvas(true);
        }, 0);
    });
}

function addPreviewOptions(nodeType) {
    chainCallback(nodeType.prototype, "getExtraMenuOptions", function(_, options) {
        let optNew = []
        const previewWidget = this.widgets.find((w) => w.name === "videopreview");
        if (!previewWidget) return;

        const PauseDesc = (previewWidget.value.paused ? "Resume" : "Pause") + " preview";
        if (previewWidget.videoEl.hidden == false) {
            optNew.push({content: PauseDesc, callback: () => {
                if (previewWidget.value.paused) {
                    previewWidget.videoEl?.play();
                } else {
                    previewWidget.videoEl?.pause();
                }
                previewWidget.value.paused = !previewWidget.value.paused;
            }});
        }

        const visDesc = (previewWidget.value.hidden ? "Show" : "Hide") + " preview";
        optNew.push({content: visDesc, callback: () => {
            if (!previewWidget.videoEl.hidden && !previewWidget.value.hidden) {
                previewWidget.videoEl.pause();
            } else if (previewWidget.value.hidden && !previewWidget.videoEl.hidden && !previewWidget.value.paused) {
                previewWidget.videoEl.play();
            }
            previewWidget.value.hidden = !previewWidget.value.hidden;
            previewWidget.parentEl.hidden = previewWidget.value.hidden;
            fitHeight(this);
        }});

        const muteDesc = (previewWidget.value.muted ? "Unmute" : "Mute") + " preview"
        optNew.push({content: muteDesc, callback: () => {
            previewWidget.value.muted = !previewWidget.value.muted
        }})

        if (options.length > 0 && options[0] != null && optNew.length > 0) {
            optNew.push(null);
        }
        options.unshift(...optNew);
    });
}

app.registerExtension({
    name: "KD_Nodes.PreviewAnimation",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData?.name !== "PreviewAnimationKD") {
            return;
        }

        // Reuse the Load Video preview widget + right-click options
        addVideoPreview(nodeType);
        addPreviewOptions(nodeType);

        // On execution, point the preview at the mp4 the backend just wrote
        chainCallback(nodeType.prototype, "onExecuted", function(message) {
            const previews = message?.kd_video;
            if (!previews || !previews.length) return;
            const p = previews[0];
            this.updateParameters({filename: p.filename, format: p.format || "video/mp4"}, true);
        });
    },
});

app.registerExtension({
    name: "KD_Nodes.LoadVideo",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData?.name !== "LoadVideoKD") {
            return;
        }

        // Add video preview widget
        addVideoPreview(nodeType);

        // Add right-click preview options (pause/hide/mute)
        addPreviewOptions(nodeType);

        // Add browse button (native file dialog via PyQt5)
        addBrowseWidget(nodeType, "video");

        // Wire up path widget callback to update preview, and widget change callbacks
        chainCallback(nodeType.prototype, "onNodeCreated", function() {
            const node = this

            // When video path changes, update preview
            const pathWidget = this.widgets.find((w) => w.name === "video");
            chainCallback(pathWidget, "callback", (value) => {
                if (!value) return;
                let extension_index = value.lastIndexOf(".");
                let extension = value.slice(extension_index + 1);
                let format = "video"
                if (["gif", "webp", "avif"].includes(extension)) {
                    format = "image"
                }
                format += "/" + extension;
                let params = {filename: value, format: format};
                this.updateParameters(params, true);
            });

            // When other widgets change, update preview params
            function update(key) {
                return function(value) {
                    let params = {}
                    params[key] = this.value
                    node?.updateParameters(params)
                }
            }
            let widgetMap = {
                'frame_load_cap': 'frame_load_cap',
                'skip_first_frames': 'skip_first_frames',
                'select_every_nth': 'select_every_nth',
            }
            for (let widget of this.widgets) {
                if (widget.name in widgetMap) {
                    chainCallback(widget, "callback", update(widgetMap[widget.name]))
                }
                if (widget.type != "button") {
                    widget.callback?.(widget.value)
                }
            }

            // Restore preview when workflow is loaded/configured
            chainCallback(this, "onConfigure", function() {
                const pw = this.widgets.find((w) => w.name === "video");
                if (pw?.value) {
                    pw.callback?.(pw.value);
                }
            });
        });
    },
});