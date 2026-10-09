/** Shared theme controller: system preference initially, explicit choice persisted across pages. */
export class ThemeController {
  constructor() {
    this.storageKey = "ainovel.theme";
    this.system = window.matchMedia("(prefers-color-scheme: dark)");
    this.buttons = [];
    this.system.addEventListener("change", () => {
      if (!this.savedTheme()) this.apply(this.system.matches ? "dark" : "light");
    });
    window.addEventListener("storage", event => {
      if (event.key === this.storageKey) this.apply(this.initialTheme());
    });
    this.apply(this.initialTheme());
  }
  savedTheme() {
    const theme = localStorage.getItem(this.storageKey);
    return theme === "dark" || theme === "light" ? theme : null;
  }
  initialTheme() { return this.savedTheme() || (this.system.matches ? "dark" : "light"); }
  apply(theme) {
    this.theme = theme;
    document.documentElement.dataset.theme = theme;
    for (const button of this.buttons) {
      button.textContent = theme === "dark" ? "☀ 浅色" : "☾ 暗黑";
      button.setAttribute("aria-label", theme === "dark" ? "切换浅色模式" : "切换暗黑模式");
      button.setAttribute("aria-pressed", String(theme === "dark"));
    }
  }
  bind(button) {
    this.buttons.push(button); this.apply(this.theme);
    button.onclick = () => {
      const next = this.theme === "dark" ? "light" : "dark";
      localStorage.setItem(this.storageKey, next); this.apply(next);
    };
  }
}

export const theme = new ThemeController();
