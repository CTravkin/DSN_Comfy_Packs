const appInput = '[data-testid="app-mode-widget-item"]';
const helpClass = "speech-app-help";

export function installAppHelp(tooltipForWidget) {
    const tooltip = document.createElement("div");
    if (typeof tooltip.showPopover !== "function") return;
    tooltip.id = "speech-app-tooltip";
    tooltip.popover = "manual";
    tooltip.setAttribute("role", "tooltip");
    tooltip.className = "rounded-md border border-border-default bg-secondary-background px-3 py-2 font-inter text-base-foreground shadow-md";

    const style = document.createElement("style");
    style.textContent = `
        ${appInput}:has(.${helpClass}) > [data-testid="app-mode-widget-description"] { display: none }
        .${helpClass} { flex: none; width: 1.5rem; height: 1.5rem; font-size: .875rem; line-height: 1 }
        #${tooltip.id} {
            position: fixed; inset: auto; margin: 0; box-sizing: border-box;
            width: max-content; max-width: min(22rem, calc(100vw - 16px)); max-height: calc(100vh - 16px);
            overflow: auto; overflow-wrap: anywhere; white-space: normal;
            font-size: .875rem; line-height: 1.4;
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
        const top = below + height <= clientHeight - 8 || above < 8 ? below : above;
        tooltip.style.left = `${Math.max(8, Math.min(anchor.left, clientWidth - width - 8))}px`;
        tooltip.style.top = `${Math.max(8, Math.min(top, clientHeight - height - 8))}px`;
    }

    function show(button, description) {
        clearTimeout(closeTimer);
        if (activeButton === button) return;
        close();
        activeButton = button;
        tooltip.textContent = description;
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
            if (!description) continue;

            const button = document.createElement("button");
            button.type = "button";
            button.className = `${helpClass} inline-flex cursor-pointer items-center justify-center rounded-md border-none bg-transparent text-muted-foreground hover:bg-secondary-background-hover hover:text-base-foreground focus-visible:ring-1 focus-visible:ring-border-default`;
            button.textContent = "ⓘ";
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
