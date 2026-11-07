const appInput = '[data-testid="app-mode-widget-item"]';
const helpClass = "speech-app-help";

export function installAppHelp(tooltipForWidget) {
    const tooltip = document.createElement("div");
    if (typeof tooltip.showPopover !== "function") return;
    tooltip.id = "speech-app-tooltip";
    tooltip.popover = "manual";
    tooltip.setAttribute("role", "tooltip");
    tooltip.className = "rounded-md border border-border-default bg-base-background px-3 py-2 font-inter text-xs/tight whitespace-pre-line text-base-foreground shadow-interface";
    const tooltipText = document.createElement("div");
    tooltip.append(tooltipText);

    const style = document.createElement("style");
    const speechInput = `${appInput}[data-speech-app-help]`;
    // App Mode reuses graph widgets, whose native tooltip is mounted outside the row.
    const widget = `${speechInput} [data-testid="node-widget"]`;
    style.textContent = `
        ${speechInput} > [data-testid="app-mode-widget-description"] { display: none }
        ${widget} > :has(> .lg-slot--input) { visibility: hidden }
        body:has(${widget} [data-grace-area-trigger][data-state$="-open"]) [data-testid="tooltip-content"] {
            visibility: hidden;
        }
        .${helpClass} { flex: none; width: 1.5rem; height: 1.5rem }
        #${tooltip.id} {
            position: fixed; inset: auto; margin: 0; box-sizing: border-box;
            width: max-content; max-width: min(24rem, calc(100vw - 16px)); overflow: visible;
        }
        #${tooltip.id} > div { max-height: calc(100vh - 34px); overflow: auto; overflow-wrap: anywhere }
        #${tooltip.id}::before {
            content: ""; position: absolute; left: var(--speech-tooltip-arrow-x); top: -4px;
            width: 7px; height: 7px; transform: translateX(-50%) rotate(45deg);
            background: inherit; border: solid var(--color-border-default); border-width: 1px 0 0 1px;
        }
        #${tooltip.id}[data-side="top"]::before {
            top: auto; bottom: -4px; border-width: 0 1px 1px 0;
        }
    `;
    document.head.append(style);
    document.body.append(tooltip);

    let activeButton;
    let pinned = false;
    let closeTimer;

    function close() {
        clearTimeout(closeTimer);
        if (!activeButton) return;
        activeButton.removeAttribute("aria-describedby");
        activeButton.setAttribute("aria-expanded", "false");
        activeButton = undefined;
        pinned = false;
        tooltip.hidePopover();
    }

    function position() {
        if (!activeButton) return;
        const anchor = activeButton.getBoundingClientRect();
        if (!activeButton.isConnected || !anchor.width) return close();
        const { clientWidth, clientHeight } = document.documentElement;
        const { width, height } = tooltip.getBoundingClientRect();
        const below = anchor.bottom + 6;
        const above = anchor.top - 6 - height;
        const showBelow = below + height <= clientHeight - 8 || above < 8;
        const top = showBelow ? below : above;
        const center = anchor.left + anchor.width / 2;
        const left = Math.max(8, Math.min(center - width / 2, clientWidth - width - 8));
        tooltip.dataset.side = showBelow ? "bottom" : "top";
        tooltip.style.setProperty("--speech-tooltip-arrow-x", `${Math.max(9, Math.min(center - left, width - 9))}px`);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${Math.max(8, Math.min(top, clientHeight - height - 8))}px`;
    }

    function show(button, description) {
        clearTimeout(closeTimer);
        if (activeButton === button) return;
        close();
        activeButton = button;
        tooltipText.textContent = description;
        button.setAttribute("aria-describedby", tooltip.id);
        button.setAttribute("aria-expanded", "true");
        tooltip.showPopover();
        position();
    }

    function scheduleClose(button) {
        if (!button || activeButton !== button || pinned || button.matches(":focus-visible, :hover")) return;
        clearTimeout(closeTimer);
        closeTimer = setTimeout(close, 150);
    }

    tooltip.addEventListener("pointerenter", () => clearTimeout(closeTimer));
    tooltip.addEventListener("pointerleave", () => scheduleClose(activeButton));
    document.addEventListener("pointerdown", event => {
        if (activeButton && !activeButton.contains(event.target) && !tooltip.contains(event.target)) close();
    }, true);
    document.addEventListener("keydown", event => {
        if (activeButton && event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            close();
        }
    }, true);
    window.addEventListener("resize", position);
    document.addEventListener("scroll", position, true);

    const rowHelp = new WeakMap();
    function sync() {
        for (const row of document.querySelectorAll(appInput)) {
            const label = row.querySelector('[data-testid="builder-widget-label"]');
            const description = label && tooltipForWidget(row.dataset.widgetKey);
            const previous = rowHelp.get(row);
            if (previous?.description === description && previous?.button === label?.nextElementSibling) continue;
            previous?.button.remove();
            rowHelp.delete(row);
            row.toggleAttribute("data-speech-app-help", Boolean(description));
            if (!description) continue;

            const button = document.createElement("button");
            button.type = "button";
            button.className = `${helpClass} inline-flex cursor-pointer items-center justify-center rounded-md border-none bg-transparent text-muted-foreground hover:bg-secondary-background-hover hover:text-base-foreground focus-visible:ring-1 focus-visible:ring-border-default`;
            const icon = document.createElement("i");
            icon.className = "icon-[lucide--info] size-4 shrink-0";
            icon.setAttribute("aria-hidden", "true");
            button.append(icon);
            button.setAttribute("aria-label", `Подсказка к полю «${label.textContent.trim()}»`);
            button.setAttribute("aria-controls", tooltip.id);
            button.setAttribute("aria-expanded", "false");
            button.addEventListener("pointerenter", event => {
                if (!pinned && event.pointerType !== "touch") show(button, description);
            });
            button.addEventListener("pointerleave", () => scheduleClose(button));
            button.addEventListener("focus", () => {
                if (button.matches(":focus-visible")) show(button, description);
            });
            button.addEventListener("blur", () => scheduleClose(button));
            button.addEventListener("click", () => {
                if (pinned && activeButton === button) close();
                else {
                    show(button, description);
                    pinned = activeButton === button;
                }
            });
            label.after(button);
            rowHelp.set(row, { button, description });
        }
        if (activeButton && !activeButton.isConnected) close();
    }

    new MutationObserver(sync).observe(document.body, {
        childList: true, subtree: true, attributes: true, attributeFilter: ["data-widget-key"]
    });
    sync();
}
