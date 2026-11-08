import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const labels = {
    "profile.model": "Модель",
    "profile.stop_after": "Остановить после запроса",
    "profile.temperature": "Температура",
    "profile.max_tokens": "Лимит токенов ответа",
    "source.text": "Промпт",
    "source.file": "Файл промпта",
    ...Object.fromEntries(Array.from({ length: 8 }, (_, index) =>
        [`files.file_${index + 1}`, `Файл ${index + 1}`])),
};
const dynamicNodes = { DSNTextRequestSetup: "profile", DSNTextPrompt: "source", DSNTextContext: "files" };
const resultFields = ["directory", "name", "include_datetime", "txt", "md"];
const textNodes = new Set([...Object.keys(dynamicNodes), "DSNTextResultSetup", "DSNTextGenerate"]);
const inputSpecs = new Map();
const resultType = "DSNTextGenerate";
const panelSelector = '[data-testid="linear-center-panel"]';
let viewKey = crypto.randomUUID();
let appResult;
let resultPanel;
let resultCard;

function migrateResultFields(graph) {
    for (const node of graph.nodes ?? []) {
        if (node.type !== "DSNTextResultSetup" || !Array.isArray(node.inputs)) continue;
        const names = node.inputs.map(input => input.name);
        if (names.join() !== "txt,md,name,include_datetime,directory") continue;
        const order = resultFields.map(name => names.indexOf(name));
        node.inputs = order.map(index => node.inputs[index]);
        if (node.widgets_values?.length === names.length) {
            node.widgets_values = order.map(index => node.widgets_values[index]);
        }
        for (const link of graph.links ?? []) {
            if (Array.isArray(link) && String(link[3]) === String(node.id)) {
                link[4] = order.indexOf(link[4]);
            } else if (!Array.isArray(link) && String(link.target_id) === String(node.id)) {
                link.target_slot = order.indexOf(link.target_slot);
            }
        }
    }
    for (const subgraph of graph.definitions?.subgraphs ?? []) migrateResultFields(subgraph);
}

function inputTooltip(type, name) {
    const specs = inputSpecs.get(type);
    if (specs?.[name]) return specs[name][1]?.tooltip;
    const [group, field] = name.split(".");
    if (!field) return;
    for (const { inputs } of specs?.[group]?.[1]?.options ?? []) {
        const spec = inputs.required?.[field] ?? inputs.optional?.[field];
        if (spec?.[1]?.tooltip) return spec[1].tooltip;
    }
}

async function catalog() {
    const response = await api.fetchApi("/text/models");
    return response.ok ? await response.json() : [];
}

function labelFields(node) {
    // The DynamicCombo frontend uses the nested input ID as its display name.
    for (const widget of node.widgets ?? []) {
        if (labels[widget.name]) widget.label = labels[widget.name];
    }
    for (const slot of node.inputs ?? []) {
        if (labels[slot.name]) slot.label = slot.localized_name = labels[slot.name];
    }
}

function resultContent(container, message, showPreview = false) {
    const title = document.createElement("strong");
    title.textContent = message.text?.[0] ?? "Текст готов";
    container.replaceChildren(title);
    const [, directory, names, preview] = message.dsn_text_result;
    if (showPreview && preview) {
        const paragraph = document.createElement("div");
        paragraph.className = "my-2 max-h-40 overflow-auto whitespace-pre-wrap break-words select-text";
        paragraph.textContent = preview;
        container.append(paragraph);
    }
    if (!directory) return;
    const path = document.createElement("div");
    path.textContent = directory;
    path.className = "break-all text-muted-foreground";
    container.append(path);
    const files = (message.files ?? []).filter(file => file.type === "output");
    if (files.length) {
        const list = document.createElement("ul");
        for (const file of files) {
            const item = document.createElement("li");
            const link = document.createElement("a");
            const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder, type: file.type });
            link.href = api.apiURL(`/view?${query}`);
            link.download = file.filename;
            link.textContent = file.filename;
            link.className = "underline";
            item.append(link);
            list.append(item);
        }
        container.append(list);
    } else {
        const note = document.createElement("div");
        note.textContent = names;
        container.append(note);
    }
}

function updateAppResult() {
    resultPanel = document.querySelector(panelSelector);
    resultCard?.remove();
    resultCard = undefined;
    if (!resultPanel || !appResult) return;
    resultCard = document.createElement("section");
    resultCard.className = "floating-panel mx-auto w-full rounded-lg p-3 text-sm";
    resultCard.style.marginBlock = "3.25rem 0.5rem";
    resultCard.style.width = "min(95%, 58rem)";
    resultContent(resultCard, appResult);
    resultPanel.prepend(resultCard);
}

app.registerExtension({
    name: "DSN.Text",
    setup() {
        const style = document.createElement("style");
        style.textContent = `
            [data-dsn-text-help] > [data-testid="app-mode-widget-description"] {
                height: auto;
                min-height: 1.25rem;
                font-size: 0.75rem;
                line-height: 1rem;
            }
            [data-dsn-text-help] > [data-testid="app-mode-widget-description"] .truncate {
                overflow: visible;
                text-overflow: clip;
                white-space: normal;
            }
        `;
        document.head.append(style);
        const getModels = api.getModels.bind(api);
        api.getModels = async (category, ...args) => category === "Text"
            ? (await catalog()).map(model => ({ name: model.filename, pathIndex: 0 }))
            : getModels(category, ...args);
        const viewMetadata = api.viewMetadata.bind(api);
        api.viewMetadata = async (category, filename, ...args) => category === "Text"
            ? (await catalog()).find(model => model.filename === filename)?.metadata ?? null
            : viewMetadata(category, filename, ...args);
        const queuePrompt = api.queuePrompt;
        api.queuePrompt = function(number, prompt, ...args) {
            if (prompt.workflow && Object.values(prompt.output ?? {}).some(node => node.class_type === resultType)) {
                const workflow = { ...prompt.workflow, extra: { ...prompt.workflow.extra, dsn_text_view: viewKey } };
                prompt = { ...prompt, workflow };
            }
            return queuePrompt.call(this, number, prompt, ...args);
        };
        api.addEventListener("executed", ({ detail }) => {
            if (detail.output?.dsn_text_result?.[0] !== viewKey) return;
            const selected = app.rootGraph?.extra?.linearData?.outputs ?? [];
            if (selected.map(String).includes(String(detail.display_node ?? detail.node))) {
                appResult = detail.output;
                updateAppResult();
            }
        });
        new MutationObserver(() => {
            if (document.querySelector(panelSelector) !== resultPanel) updateAppResult();
            for (const row of document.querySelectorAll('[data-testid="app-mode-widget-item"]')) {
                const parts = (row.dataset.widgetKey ?? "").split(":");
                const node = app.rootGraph?.getNodeById?.(parts[1]);
                row.toggleAttribute("data-dsn-text-help", textNodes.has(node?.type));
            }
        }).observe(document.body, {
            childList: true, subtree: true, attributes: true, attributeFilter: ["data-widget-key"],
        });
    },
    beforeConfigureGraph(graph) {
        migrateResultFields(graph);
        const nodes = new Map((graph.nodes ?? []).map(node => [String(node.id), node.type]));
        for (const entry of graph.extra?.linearData?.inputs ?? []) {
            if (!Array.isArray(entry)) continue;
            const [key, name, config] = entry;
            const parts = String(key).split(":");
            const id = parts.length === 1 ? parts[0] : parts[1];
            const tooltip = inputTooltip(nodes.get(id), name);
            if (tooltip) entry[2] = { ...config, description: tooltip };
        }
    },
    afterConfigureGraph() {
        viewKey = crypto.randomUUID();
        appResult = undefined;
        updateAppResult();
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (textNodes.has(nodeData.name)) {
            inputSpecs.set(nodeData.name, { ...nodeData.input.required, ...nodeData.input.optional });
        }
        const dynamicName = dynamicNodes[nodeData.name];
        if (dynamicName) {
            const onCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function(...args) {
                const result = onCreated?.apply(this, args);
                const node = this;
                const combo = node.widgets?.find(widget => widget.name === dynamicName);
                if (combo) {
                    const callback = combo.callback;
                    combo.callback = function(...args) {
                        const result = callback?.apply(this, args);
                        labelFields(node);
                        return result;
                    };
                }
                labelFields(node);
                return result;
            };
            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function(...args) {
                const result = onConfigure?.apply(this, args);
                labelFields(this);
                return result;
            };
        }
        if (nodeData.name === resultType) {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function(message) {
                onExecuted?.call(this, message);
                if (message?.dsn_text_result?.[0] !== viewKey || !this.addDOMWidget) return;
                if (!this.dsnTextResult) {
                    this.dsnTextResult = document.createElement("div");
                    this.dsnTextResult.className = "p-2 text-sm";
                    this.addDOMWidget("Результат", "dsn-text-result", this.dsnTextResult,
                                      { serialize: false, hideOnZoom: true });
                }
                resultContent(this.dsnTextResult, message, true);
                this.setDirtyCanvas(true, true);
            };
        }
    },
});
