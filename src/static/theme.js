function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  localStorage.setItem("yt-dl-theme", next);
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({theme:next})});
}
const savedTheme = localStorage.getItem("yt-dl-theme");
if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);
