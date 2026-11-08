import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const labels = {
    "profile.model": "Модель",
    "profile.stop_after": "Остановить после запроса",
    "profile.temperature": "Температура",
    "profile.max_tokens": "Лимит токенов ответа",
    "source.text": "Промпт",
    "source.file": "Файл промпта",
};
const dynamicNodes = { DSNTextRequestSetup: "profile", DSNTextPrompt: "source" };
const resultType = "DSNTextGenerate";
const panelSelector = '[data-testid="linear-center-panel"]';
let viewKey = crypto.randomUUID();
let appResult;
let resultPanel;
let resultCard;

function labelFields(node) {
    // The DynamicCombo frontend uses the nested input ID as its display name.
    for (const widget of node.widgets ?? []) {
        if (labels[widget.name]) widget.label = labels[widget.name];
    }
    for (const slot of node.inputs ?? []) {
        if (labels[slot.name]) slot.label = slot.localized_name = labels[slot.name];
    }
}

function resultContent(container, message) {
    const title = document.createElement("strong");
    title.textContent = message.text?.[0] ?? "Текст готов";
    container.replaceChildren(title);
    const [, directory, names] = message.dsn_text_result;
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
        }).observe(document.body, { childList: true, subtree: true });
    },
    afterConfigureGraph() {
        viewKey = crypto.randomUUID();
        appResult = undefined;
        updateAppResult();
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
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
                resultContent(this.dsnTextResult, message);
                this.setDirtyCanvas(true, true);
            };
        }
    },
});
