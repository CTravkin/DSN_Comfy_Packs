import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const localHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const panelSelector = '[data-testid="linear-center-panel"]';
const nodeWidgets = new WeakMap();
const pending = new Map();
const earlyCompletion = new Map();
const resultType = "DSNTextGenerate";
let submitting = 0;
let viewKey;
let panel;
let card;
let appResult;

function element(tag, text) {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    return item;
}

function fileList(exports) {
    const list = element("ul");
    list.className = "text-result-files";
    for (const [index, { name, file }] of exports.entries()) {
        const row = element("li");
        const branch = element("span", index === exports.length - 1 ? "└─" : "├─");
        branch.className = "text-result-branch text-muted-foreground";
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
        link.download = file.filename;
        link.className = "text-result-link";
        row.append(link);
    }
    return list;
}

function resultActions(result, inApp) {
    const section = element("section");
    section.className = "text-result-actions font-inter text-base-foreground";
    if (inApp) section.classList.add("text-result-card", "floating-panel");
    const heading = element("h3", "Результат");
    heading.className = "text-result-heading";
    section.append(heading);
    if (result.exports?.length) {
        const folder = element("div", result.directory);
        folder.className = "text-result-path";
        section.append(folder, fileList(result.exports));
    }
    if (result.folder_token && localHosts.has(location.hostname)) {
        const button = element("button");
        button.type = "button";
        button.className = "text-result-button inline-flex cursor-pointer appearance-none items-center gap-2 rounded-md border-none bg-secondary-background px-2 font-inter font-medium text-base-foreground transition-colors hover:bg-secondary-background-hover focus-visible:ring-1 focus-visible:ring-border-default focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50";
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
                const response = await api.fetchApi("/text/results/open-folder", {
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
    if (result.error || result.detail) {
        const note = element("p", result.error || result.detail);
        if (result.error) {
            note.className = "text-destructive text-result-error";
            note.setAttribute("role", "alert");
        }
        section.append(note);
    }
    if (!inApp && result.preview) {
        const preview = element("div", result.preview);
        preview.className = "text-result-preview";
        section.append(preview);
    }
    return section;
}

function renderApp() {
    panel = document.querySelector(panelSelector);
    card?.remove();
    card = undefined;
    if (panel && (appResult?.error || appResult?.exports?.length)) {
        card = resultActions(appResult, true);
        panel.prepend(card);
    }
}

export function showNodeResult(node, result) {
    if (!node?.addDOMWidget) return;
    let body = nodeWidgets.get(node);
    if (!result && !body) return;
    if (!body) {
        body = element("div");
        const widget = node.addDOMWidget("Результат", "dsn-text-result", body, { serialize: false, hideOnZoom: true });
        widget.serializeValue = () => undefined;
        nodeWidgets.set(node, body);
    }
    body.replaceChildren(...(result ? [resultActions(result, false)] : []));
    node.setDirtyCanvas(true, true);
}

function displayResult(result, ids) {
    const graph = app.rootGraph;
    for (const id of ids) {
        const node = graph?.getNodeById(id);
        if (node?.type === resultType) showNodeResult(node, result);
    }
    const selected = (graph?.extra?.linearData?.outputs ?? []).map(String);
    if (ids.some(id => selected.includes(id))) {
        appResult = result;
        renderApp();
    }
}

export function currentResult(message) {
    try {
        const result = JSON.parse(message?.dsn_text_result?.[0]);
        return viewKey && result.view_key === viewKey ? result : undefined;
    } catch {
        return undefined;
    }
}

export function resetResults() {
    viewKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
    pending.clear();
    earlyCompletion.clear();
    appResult = undefined;
    renderApp();
}

export function installResults() {
    const style = element("style");
    style.textContent = `
        .text-result-actions {
            box-sizing: border-box; display: grid; justify-items: start; gap: .25rem;
            min-width: 0; padding: .375rem .5rem; font-size: .75rem; line-height: 1rem;
        }
        .text-result-heading { margin: 0; font: inherit; font-weight: 500; }
        .text-result-actions > p { margin: 0; }
        .text-result-path { max-width: 100%; overflow-wrap: anywhere; user-select: text; }
        .text-result-files { display: grid; gap: .125rem; max-width: 100%; margin: 0; padding: 0; list-style: none; }
        .text-result-error { overflow-wrap: anywhere; }
        .text-result-preview {
            width: 100%; max-height: 12rem; overflow: auto;
            white-space: pre-wrap; overflow-wrap: anywhere; user-select: text;
        }
        .text-result-files > li { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .375rem; overflow-wrap: anywhere; }
        .text-result-branch { font-family: var(--font-mono, monospace); user-select: none; }
        .text-result-link { color: inherit; text-decoration: none; }
        .text-result-link:is(:hover, :focus-visible) { text-decoration: underline; text-underline-offset: .15em; }
        .text-result-button { min-height: 1.5rem; margin-top: .25rem; font-size: inherit; }
        .text-result-actions [role="status"]:empty { display: none; }
        .text-result-card {
            flex-shrink: 0; position: relative; z-index: 2;
            align-self: stretch; margin: 3.75rem .5rem 0; padding: .625rem .75rem;
            font-size: .875rem; line-height: 1.25rem;
        }
        .text-result-card .text-result-heading { font-size: 1rem; line-height: 1.5rem; font-weight: 700; }
        .text-result-card .text-result-button { min-height: 1.75rem; }
        .text-result-card ~ [data-testid="linear-output-info"] { padding-block-start: 0; }
        /* Comfy can restore an earlier preview when returning from the graph after a failure. */
        ${panelSelector}:has(> .text-result-card .text-result-error)
            > :not(.text-result-card, .absolute, [data-testid="linear-header-progress-bar"]) { display: none; }
    `;
    document.head.append(style);
    resetResults();
    const queue = api.queuePrompt.bind(api);
    api.queuePrompt = async function(number, prompt, ...args) {
        const ids = Object.entries(prompt.output ?? {}).filter(([, node]) => node.class_type === resultType).map(([id]) => id);
        const workflow = prompt.workflow;
        if (!ids.length || !workflow || workflow.id !== app.rootGraph?.id) return queue(number, prompt, ...args);
        const key = viewKey;
        const extra = { ...workflow.extra, dsn_text_view: key };
        displayResult(undefined, ids);
        submitting++;
        try {
            const response = await queue(number, { ...prompt, workflow: { ...workflow, extra } }, ...args);
            if (key === viewKey && response?.prompt_id) {
                if (earlyCompletion.has(response.prompt_id)) {
                    const error = earlyCompletion.get(response.prompt_id);
                    if (error) displayResult({ error }, ids);
                } else pending.set(response.prompt_id, ids);
            }
            return response;
        } catch (error) {
            const message = error?.response?.error?.message ?? error?.message;
            if (key === viewKey) displayResult({ error: message || "Не удалось запустить генерацию" }, ids);
            throw error;
        } finally {
            if (--submitting === 0) earlyCompletion.clear();
        }
    };
    api.addEventListener("executed", ({ detail }) => {
        const result = currentResult(detail.output);
        if (result) displayResult(result, [String(detail.display_node ?? detail.node)]);
    });
    function complete(promptId, error) {
        const ids = pending.get(promptId);
        if (ids) {
            pending.delete(promptId);
            if (error) displayResult({ error }, ids);
        } else if (submitting && promptId) {
            earlyCompletion.set(promptId, error);
        }
    }
    api.addEventListener("execution_error", ({ detail }) => {
        complete(detail.prompt_id, detail.exception_message?.trim() || "Генерация не завершилась");
    });
    api.addEventListener("execution_interrupted", ({ detail }) => {
        complete(detail.prompt_id, "Генерация остановлена");
    });
    api.addEventListener("execution_success", ({ detail }) => complete(detail.prompt_id));
    new MutationObserver(() => {
        if (document.querySelector(panelSelector) !== panel) renderApp();
    }).observe(document.body, { childList: true, subtree: true });
}
