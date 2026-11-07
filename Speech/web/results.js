import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const runs = new Map();
const nodeWidgets = new WeakMap();
const localHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const panelSelector = '[data-testid="linear-center-panel"]';
let panel;
let appActions;
let viewKey;

function newViewKey() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
}

function element(tag, text) {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    return item;
}

function resultActions(result, inApp) {
    const section = element("section");
    section.className = "speech-result-actions";
    const title = inApp ? "Файлы последнего запуска" : result.summary;
    section.append(element("strong", title));
    const list = element("div");
    list.className = "speech-result-files";
    for (const { name, file } of result.exports) {
        if (!file || file.type !== "output") {
            list.append(element("span", name));
            continue;
        }
        const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder, type: file.type });
        const link = element("a", name);
        link.href = api.apiURL(`/view?${query}`);
        link.download = name;
        list.append(link);
    }
    section.append(list);
    const folder = element("small", result.directory);
    folder.title = result.directory;
    section.append(folder);
    if (result.folder_token && localHosts.has(location.hostname)) {
        const button = element("button", "Открыть папку");
        button.type = "button";
        const status = element("small");
        status.setAttribute("role", "status");
        button.addEventListener("click", async () => {
            button.disabled = true;
            status.textContent = "";
            try {
                const response = await api.fetchApi("/speech/results/open-folder", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ token: result.folder_token })
                });
                if (!response.ok) throw new Error(String(response.status));
            } catch {
                status.textContent = "Не удалось открыть папку";
            } finally {
                button.disabled = false;
            }
        });
        section.append(button, status);
    }
    return section;
}

function updateAppActions() {
    panel = document.querySelector(panelSelector);
    appActions?.remove();
    appActions = undefined;
    if (!panel) return;
    const selected = app.rootGraph?.extra?.linearData?.outputs ?? [];
    const result = selected.map(id => runs.get(String(id))).find(Boolean);
    if (result) {
        appActions = resultActions(result, true);
        panel.prepend(appActions);
    }
}

export function currentResult(message) {
    try {
        const result = JSON.parse(message?.speech_result?.[0]);
        return viewKey && result.view_key === viewKey ? result : undefined;
    } catch {
        return undefined;
    }
}

export function installResultActions(speechNodes) {
    viewKey ??= newViewKey();
    const queuePrompt = api.queuePrompt.bind(api);
    api.queuePrompt = function(number, prompt, ...args) {
        // Graph copies can share IDs. Only decorate the submission for this view.
        if (prompt.workflow?.id === app.rootGraph?.id &&
                Object.values(prompt.output ?? {}).some(node => Object.hasOwn(speechNodes, node.class_type))) {
            const extra = { ...prompt.workflow.extra, speech_view_key: viewKey };
            prompt = { ...prompt, workflow: { ...prompt.workflow, extra } };
        }
        return queuePrompt(number, prompt, ...args);
    };
    const style = element("style");
    style.textContent = `
        .speech-result-actions { display: flex; flex-direction: column; align-items: flex-start; gap: .35rem; padding: .5rem .75rem; overflow: hidden; }
        .speech-result-actions small { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .speech-result-files { display: flex; flex-wrap: wrap; gap: .25rem .75rem; overflow-wrap: anywhere; }
        .speech-result-files a { text-decoration: underline; }
        .speech-result-actions button { font: inherit; cursor: pointer; border: 1px solid currentColor; border-radius: .35rem; padding: .2rem .5rem; }
        ${panelSelector} > .speech-result-actions { margin: 3.25rem .75rem 0; flex-shrink: 0; position: relative; z-index: 2; border: 1px solid var(--color-border-subtle); border-radius: .5rem; }
    `;
    document.head.append(style);
    api.addEventListener("executed", ({ detail }) => {
        const result = currentResult(detail.output);
        if (!result) return;
        runs.set(String(detail.display_node ?? detail.node), result);
        updateAppActions();
    });
    new MutationObserver(() => {
        if (document.querySelector(panelSelector) !== panel) updateAppActions();
    }).observe(document.body, { childList: true, subtree: true });
    updateAppActions();
}

export function refreshResultActions() {
    viewKey = newViewKey();
    runs.clear();
    queueMicrotask(updateAppActions);
}

export function showNodeResult(node, result) {
    if (typeof node.addDOMWidget !== "function") return;
    let body = nodeWidgets.get(node);
    if (!body) {
        body = element("div");
        const widget = node.addDOMWidget("Результат", "speech-result", body, { serialize: false, hideOnZoom: true });
        widget.serializeValue = () => undefined;
        nodeWidgets.set(node, body);
    }
    body.replaceChildren(resultActions(result, false));
    node.setDirtyCanvas(true, true);
}
