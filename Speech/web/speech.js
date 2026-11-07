import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import { currentResult, installResultActions, refreshResultActions, showNodeResult } from "./results.js";

// Display order only; widget positions and numbered sockets in saved graphs stay stable.
const fields = {
    FileSetup: "media_path output_directory name include_datetime",
    WhisperTranscribe: "model device task language language_code prompt beam_size vad existing_json save_text save_json srt vtt",
    SpeakerTranscript: "recognition_json diarization_json speaker_map confirmed max_gap save_text save_json",
    SpeakerDiarization: "model device speakers min_speakers max_speakers existing_json rttm",
    VoiceprintExtract: "model device start end",
    VoiceprintIdentify: "model device reference_root diarization_json threshold minimum_margin",
    VoiceprintCompare: "left right"
};
const category = "Speech";
const deviceLabels = { Видеокарта: "GPU (Видеокарта)", Процессор: "CPU (Процессор)" };
const legacyLabels = {
    "Файл и сохранение": "Работа с файлами",
    "Папка результата": "Каталог результатов",
    "Название результата": "Имя результата",
    "Добавлять дату и время": "Дата и время в имени",
    "Код другого языка": "Код языка",
    "Сохранять JSON в корень": "Сохранить JSON",
    "Сохранять текст": "Сохранить TXT",
    "Сохранять RTTM": "Сохранить RTTM",
    "Фильтр неречевых участков": "Фильтрация участков без речи",
    "Имена подтверждены": "Подтверждение имен",
    "Объединять паузы до, с": "Объединять паузы, с"
};

function installNodeLayout() {
    const canvas = app.canvasContainer;
    if (!canvas) return;
    const nodeBody = ':not([data-collapsed]) [data-testid^="node-body-"]';
    const body = `[data-speech-node]${nodeBody}`;
    const slots = `${body} > .justify-between:first-child`;
    const widgets = '[data-testid="node-widgets"]';
    const fileSetup = `[data-speech-node="FileSetup"]${nodeBody}`;
    const fileOutputs = `${fileSetup} > .justify-between:first-child > .ml-auto`;
    const style = document.createElement("style");
    style.textContent = `
        ${slots} { display: contents }
        ${slots} > :not(.ml-auto) { order: -2 }
        ${slots} > .ml-auto { order: 1 }
        ${body} > .mt-auto { order: 101 }
        [data-speech-node="SpeakerDiarization"]${nodeBody} > .justify-between:first-child > .ml-auto { flex-direction: column-reverse }
        ${body} > ${widgets} { grid-template-rows: none !important; align-content: start }
        ${body} > ${widgets} > :has(> [data-widget-name]:not(.lg-node-widget), > [data-widget-name] > .opacity-100) { order: -1 !important }
        ${Object.entries(fields).flatMap(([type, names]) => names.split(" ").map((name, index) =>
            `[data-speech-node="${type}"]${nodeBody} > ${widgets} > :has(> [data-widget-name="${name}"]) { order: ${index * 2}; }`
        )).join("\n")}
        ${body} > ${widgets} > :has(> [data-widget-name="Результат"]) { order: 100 }
        ${fileSetup} { display: grid; grid-template-columns: min-content minmax(80px, min-content) minmax(125px, 1fr) }
        ${fileSetup} > ${widgets}, ${fileOutputs} { display: contents }
        ${fileSetup} > ${widgets} > * { padding-right: .75rem }
        ${fileSetup} > .mt-auto, ${fileOutputs} > * { grid-column: 1 / -1 }
        ${fileOutputs} > * { justify-self: end; order: 98 }
        ${fileOutputs} > :has([data-slot-key$="-out-2"]) { order: 1 }
        ${fileSetup}:has(> ${widgets} [data-widget-name="output_directory"].lg-node-widget)
            > .justify-between:first-child > .ml-auto > :has([data-slot-key$="-out-1"]) { order: 3 }
    `;
    document.head.append(style);
    function markNodes() {
        const graph = app.canvas?.graph;
        for (const element of canvas.querySelectorAll(".lg-node[data-node-id]")) {
            const type = graph?.getNodeById(element.dataset.nodeId)?.type;
            if (Object.hasOwn(fields, type)) {
                if (element.dataset.speechNode !== type) element.dataset.speechNode = type;
            } else if (element.dataset.speechNode) {
                delete element.dataset.speechNode;
            }
        }
    }
    // Vue creates and reuses node elements when a workflow or subgraph opens.
    new MutationObserver(markNodes).observe(canvas, { subtree: true, childList: true,
        attributes: true, attributeFilter: ["data-node-id"] });
    markNodes();
}

let catalogPromise;
function catalog(refresh = false) {
    if (refresh || !catalogPromise) {
        catalogPromise = api.fetchApi("/speech/catalog").then(async response => {
            if (!response.ok) throw new Error("Не удалось прочитать каталог моделей речи");
            return response.json();
        }).catch(error => { catalogPromise = undefined; throw error; });
    }
    return catalogPromise;
}

app.registerExtension({
    name: "Speech.ModelsAndResults",
    async setup() {
        installNodeLayout();
        installResultActions(fields);
        // Adapt only our category. Other model libraries and metadata stay native.
        const getModels = api.getModels.bind(api);
        api.getModels = async function(folder, ...args) {
            if (folder !== category) return getModels(folder, ...args);
            return (await catalog(true)).models.map(model => ({ name: model.filename, pathIndex: 0 }));
        };
        const viewMetadata = api.viewMetadata.bind(api);
        api.viewMetadata = async function(folder, filename, ...args) {
            if (folder !== category) return viewMetadata(folder, filename, ...args);
            return (await catalog()).models.find(model => model.filename === filename)?.metadata ?? null;
        };
        // The backend also registers on startup. This idempotent retry handles a
        // native asset database that becomes ready after custom-node loading.
        try {
            const response = await api.fetchApi("/speech/catalog/register", { method: "POST" });
            if (!response.ok) console.warn("Библиотека моделей речи: регистрация пока недоступна");
        } catch {
            console.warn("Библиотека моделей речи: соединение пока недоступно");
        }
    },
    afterConfigureGraph() {
        refreshResultActions();
    },
    nodeCreated(node) {
        if (!Object.hasOwn(fields, node.comfyClass ?? node.type)) return;
        const device = node.widgets?.find(widget => widget.name === "device");
        if (device) device.options.getOptionLabel = value => deviceLabels[value] ?? value;
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!Object.hasOwn(fields, nodeData.name)) return;
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function(info) {
            configure?.apply(this, arguments);
            function updateLabel(slot, label) {
                if (label && (!slot.label || legacyLabels[slot.label] === label)) slot.label = label;
            }
            for (const input of this.inputs ?? []) {
                const spec = nodeData.input.required?.[input.name] ?? nodeData.input.optional?.[input.name];
                updateLabel(input, spec?.[1]?.display_name);
            }
            (this.outputs ?? []).forEach((output, index) => updateLabel(output, nodeData.output_name[index]));
            if (this.type === "VoiceprintIdentify" && info?.title === "Voiceprint Identify") {
                this.title = nodeData.display_name;
            }
        };
        // Older runs may still contain a directory preview for File Setup.
        if (nodeData.name === "FileSetup") return;

        const original = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function(message) {
            original?.apply(this, arguments);
            if (message?.speech_result) {
                const result = currentResult(message);
                if (result) showNodeResult(this, result);
                return;
            }
            if (!Array.isArray(message?.text)) return;
            let widget = this.widgets?.find(item => item.name === "Результат");
            if (!widget) {
                widget = ComfyWidgets.STRING(this, "Результат", ["STRING", { multiline: true }], app).widget;
                widget.options ??= {};
                widget.options.readOnly = true;
                widget.options.serialize = false;
                widget.serializeValue = () => undefined;
                if (widget.inputEl) widget.inputEl.readOnly = true;
            }
            widget.value = message.text.join("\n");
            this.setSize([Math.max(this.size[0], 320), Math.max(this.size[1], this.computeSize()[1])]);
            this.setDirtyCanvas(true, true);
        };
    }
});
