try {
  const value = localStorage.getItem("obot-ep.theme");
  const preference = ["light", "dark"].includes(value) ? value : "system";
  document.documentElement.dataset.theme = preference;
  if (
    preference === "dark" ||
    (preference === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  )
    document.documentElement.classList.add("dark");
} catch {
  document.documentElement.dataset.theme = "system";
}
