import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { installAppHelp } from "./app-help.js";
import { currentResult, installResults, resetResults, showNodeResult } from "./results.js";

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
const textNodes = new Set([...Object.keys(dynamicNodes), "DSNTextResultSetup", "DSNTextGenerate", "DSNTextCollect"]);
const inputSpecs = new Map();
const resultType = "DSNTextGenerate";
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

function appWidgetTooltip(key) {
    const graph = app.rootGraph;
    if (!key || !graph) return;
    const parts = key.split(":");
    if (parts.length !== 3 || parts[0] !== graph.id) return;
    return inputTooltip(graph.getNodeById(parts[1])?.type, parts[2]);
}

function migrateContextSlots(graph) {
    for (const node of graph.nodes ?? []) {
        if (node.type !== "DSNTextContext") continue;
        for (const slot of node.inputs ?? []) {
            if (slot.type === "COMBO" && /^files\.file_\d+$/.test(slot.name)) slot.type = "STRING";
        }
    }
    for (const subgraph of graph.definitions?.subgraphs ?? []) migrateContextSlots(subgraph);
}

function installNodeLayout() {
    const canvas = app.canvasContainer;
    if (!canvas) return;
    const body = '[data-dsn-text-node]:not([data-collapsed]) [data-testid^="node-body-"]';
    const slots = `${body} > .justify-between:first-child`;
    const style = document.createElement("style");
    style.textContent = `
        ${slots} { display: contents }
        ${slots} > :not(.ml-auto) { order: -2 }
        ${slots} > .ml-auto { order: 1 }
        ${body} > .mt-auto { order: 101 }
        ${body} > [data-testid="node-widgets"] { grid-template-rows: none !important; align-content: start }
    `;
    document.head.append(style);
    function markNodes() {
        const graph = app.canvas?.graph;
        for (const element of canvas.querySelectorAll(".lg-node[data-node-id]")) {
            element.toggleAttribute("data-dsn-text-node", textNodes.has(graph?.getNodeById(element.dataset.nodeId)?.type));
        }
    }
    new MutationObserver(markNodes).observe(canvas, {
        subtree: true, childList: true, attributes: true, attributeFilter: ["data-node-id"],
    });
    markNodes();
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

app.registerExtension({
    name: "DSN.Text",
    setup() {
        installAppHelp(appWidgetTooltip);
        installNodeLayout();
        installResults();
        const getModels = api.getModels.bind(api);
        api.getModels = async (category, ...args) => category === "Text"
            ? (await catalog()).map(model => ({ name: model.filename, pathIndex: 0 }))
            : getModels(category, ...args);
        const viewMetadata = api.viewMetadata.bind(api);
        api.viewMetadata = async (category, filename, ...args) => category === "Text"
            ? (await catalog()).find(model => model.filename === filename)?.metadata ?? null
            : viewMetadata(category, filename, ...args);
    },
    beforeConfigureGraph(graph) {
        migrateResultFields(graph);
        migrateContextSlots(graph);
    },
    afterConfigureGraph: resetResults,
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
                const result = currentResult(message);
                if (result) showNodeResult(this, result);
            };
        }
    },
});
