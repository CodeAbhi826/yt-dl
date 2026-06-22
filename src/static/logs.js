function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

let autoScroll = true;
let currentLevel = "ALL";
const container = document.getElementById("log-container");
const linesDiv = document.getElementById("log-lines");

function appendLog(entry) {
  if (currentLevel !== "ALL" && entry.level !== currentLevel) return;
  const div = document.createElement("div");
  div.className = "log-line log-" + entry.level;
  div.innerHTML = "<span class=\"log-time\">" + escapeHtml(entry.time) + "</span><span class=\"log-msg\">" + escapeHtml(entry.message) + "</span>";
  linesDiv.appendChild(div);
  while (linesDiv.children.length > 500) linesDiv.removeChild(linesDiv.firstChild);
  if (autoScroll) container.scrollTop = container.scrollHeight;
}

function filterLogs() {
  currentLevel = document.getElementById("log-level").value;
  linesDiv.innerHTML = "";
  fetch("/api/logs?level=" + currentLevel + "&count=100").then(r => r.json()).then(data => data.forEach(appendLog));
}

function clearLogs() { linesDiv.innerHTML = ""; }

function toggleAutoScroll() {
  autoScroll = !autoScroll;
  document.getElementById("autoscroll-btn").textContent = "Auto-scroll: " + (autoScroll ? "ON" : "OFF");
}

const evtSource = new EventSource("/api/logs/stream");
evtSource.onmessage = function(e) { appendLog(JSON.parse(e.data)); };
filterLogs();
