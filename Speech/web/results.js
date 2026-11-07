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
    section.className = "speech-result-actions font-inter text-base-foreground";
    if (inApp) section.classList.add("speech-result-card", "floating-panel");
    const heading = element("h3", "Результат");
    heading.className = "speech-result-heading";
    const folder = element("div", result.directory);
    folder.className = "speech-result-path";
    section.append(heading, folder);
    const list = element("ul");
    list.className = "speech-result-files";
    for (const [index, { name, file }] of result.exports.entries()) {
        const row = element("li");
        const branch = element("span", index === result.exports.length - 1 ? "└─" : "├─");
        branch.className = "speech-result-branch text-muted-foreground";
        branch.setAttribute("aria-hidden", "true");
        row.append(branch);
        list.append(row);
        if (!file || file.type !== "output") {
            row.append(element("span", name));
            continue;
        }
        const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder, type: file.type });
        const link = element("a", name);
        link.href = api.apiURL(`/view?${query}`);
        link.download = name;
        link.className = "speech-result-link";
        row.append(link);
    }
    if (result.exports.length) section.append(list);
    if (result.folder_token && localHosts.has(location.hostname)) {
        const button = element("button");
        button.type = "button";
        button.className = "speech-result-button inline-flex cursor-pointer appearance-none items-center gap-2 rounded-md border-none bg-secondary-background px-2 font-inter font-medium text-base-foreground transition-colors hover:bg-secondary-background-hover focus-visible:ring-1 focus-visible:ring-border-default focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50";
        const icon = element("span");
        icon.className = "icon-[lucide--folder-open] size-4 shrink-0";
        icon.setAttribute("aria-hidden", "true");
        button.append(icon, element("span", "Открыть папку"));
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
        .speech-result-actions {
            box-sizing: border-box; display: grid; justify-items: start; gap: .25rem;
            min-width: 0; padding: .375rem .5rem; font-size: .75rem; line-height: 1rem;
        }
        .speech-result-heading { margin: 0; font: inherit; font-weight: 500; }
        .speech-result-path { max-width: 100%; overflow-wrap: anywhere; user-select: text; }
        .speech-result-files { display: grid; gap: .125rem; max-width: 100%; margin: 0; padding: 0; list-style: none; }
        .speech-result-files > li { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .375rem; overflow-wrap: anywhere; }
        .speech-result-branch { font-family: var(--font-mono, monospace); user-select: none; }
        .speech-result-link { color: inherit; text-decoration: none; }
        .speech-result-link:is(:hover, :focus-visible) { text-decoration: underline; text-underline-offset: .15em; }
        .speech-result-button { min-height: 1.5rem; margin-top: .25rem; font-size: inherit; }
        .speech-result-actions [role="status"]:empty { display: none; }
        .speech-result-card {
            flex-shrink: 0; position: relative; z-index: 2;
            align-self: stretch; margin: 3.75rem .5rem 0; padding: .625rem .75rem;
            font-size: .875rem; line-height: 1.25rem;
        }
        .speech-result-card .speech-result-heading { font-size: 1rem; line-height: 1.5rem; font-weight: 700; }
        .speech-result-card .speech-result-button { min-height: 1.75rem; }
        .speech-result-card ~ [data-testid="linear-output-info"] { padding-block-start: 0; }
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
