// Light / dark mode. The page's <head> sets data-theme before anything is drawn
// (so there's no flash); this adds the switch. A choice is remembered in this
// browser; with no choice the page follows the computer's own setting.
const KEY = "creative-lens-theme";
const root = document.documentElement;
const system = matchMedia("(prefers-color-scheme: light)");

function saved() {
  try { return localStorage.getItem(KEY); } catch { return null; }
}
function apply(theme) {
  root.dataset.theme = theme;
  for (const b of document.querySelectorAll("[data-theme-toggle]")) {
    const toLight = theme === "dark";
    b.querySelector(".theme-icon").textContent = toLight ? "☀" : "☾";
    b.querySelector(".theme-label").textContent = toLight ? "Light mode" : "Dark mode";
    b.setAttribute("aria-label", toLight ? "Switch to light mode" : "Switch to dark mode");
  }
}

apply(root.dataset.theme || saved() || (system.matches ? "light" : "dark"));
system.addEventListener("change", (e) => { if (!saved()) apply(e.matches ? "light" : "dark"); });
for (const b of document.querySelectorAll("[data-theme-toggle]")) {
  b.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.classList.add("theme-switching"); // colours fade instead of jumping
    apply(next);
    try { localStorage.setItem(KEY, next); } catch { /* private window: works until the page closes */ }
    setTimeout(() => root.classList.remove("theme-switching"), 400);
  });
}
