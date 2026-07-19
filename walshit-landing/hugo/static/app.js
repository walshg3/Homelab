const dialog = document.querySelector("#help-dialog");
const closeButton = dialog.querySelector(".close");
const header = document.querySelector("header");
const navToggle = document.querySelector(".nav-toggle");
const primaryNavigation = document.querySelector("#primary-navigation");
const compactNavigation = window.matchMedia("(max-width: 1024px)");

function setNavigationOpen(open, returnFocus = false) {
  const expanded = compactNavigation.matches && open;
  navToggle.setAttribute("aria-expanded", String(expanded));
  navToggle.setAttribute("aria-label", expanded ? "Close menu" : "Open menu");
  primaryNavigation.hidden = compactNavigation.matches && !expanded;
  if (returnFocus) navToggle.focus();
}

function syncNavigation() {
  setNavigationOpen(false);
}

navToggle.addEventListener("click", () => {
  setNavigationOpen(navToggle.getAttribute("aria-expanded") !== "true");
});

primaryNavigation.addEventListener("click", (event) => {
  if (event.target.closest("a, button")) setNavigationOpen(false);
}, { capture: true });

document.addEventListener("click", (event) => {
  if (navToggle.getAttribute("aria-expanded") === "true" && !header.contains(event.target)) {
    setNavigationOpen(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true") {
    setNavigationOpen(false, true);
  }
});

compactNavigation.addEventListener("change", syncNavigation);
syncNavigation();

document.querySelectorAll(".help-trigger").forEach((button) => {
  button.addEventListener("click", () => dialog.showModal());
});

closeButton.addEventListener("click", () => dialog.close());

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) {
    dialog.close();
  }
});
