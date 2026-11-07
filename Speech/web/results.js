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
    section.className = "speech-result-actions font-inter text-sm";
    const heading = element("h3", inApp ? "Результат" : result.summary);
    heading.className = "font-semibold text-base-foreground";
    const folder = element("div", result.directory);
    folder.className = "speech-result-path text-muted-foreground";
    section.append(heading, folder);
    const list = element("ul");
    list.className = "speech-result-files";
    for (const { name, file } of result.exports) {
        const row = element("li");
        list.append(row);
        if (!file || file.type !== "output") {
            row.textContent = name;
            continue;
        }
        const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder, type: file.type });
        const link = element("a", name);
        link.href = api.apiURL(`/view?${query}`);
        link.download = name;
        link.className = "text-base-foreground underline hover:text-muted-foreground";
        row.append(link);
    }
    if (result.exports.length) section.append(list);
    if (result.folder_token && localHosts.has(location.hostname)) {
        const button = element("button", "Открыть папку");
        button.type = "button";
        button.className = "inline-flex h-8 items-center justify-center rounded-md bg-secondary-background px-3 font-medium text-base-foreground transition-colors hover:bg-secondary-background-hover focus-visible:ring-1 focus-visible:ring-border-default focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50";
        const status = element("small");
        status.className = "text-muted-foreground";
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
        .speech-result-path, .speech-result-files { max-width: 100%; overflow-wrap: anywhere; }
        .speech-result-files { display: grid; gap: .15rem; margin: 0; padding-inline-start: .85rem; list-style: none; }
        .speech-result-actions [role="status"]:empty { display: none; }
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
