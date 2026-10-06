// Before first paint, so there is no flash of the wrong theme. A separate
// file, not inline, so the Content-Security-Policy can forbid inline scripts.
document.documentElement.dataset.theme = localStorage.getItem("theme")
  || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
