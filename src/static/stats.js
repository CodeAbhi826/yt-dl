function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function showToast(message, type) {
  const toast = document.createElement("div");
  toast.style.cssText = "position:fixed;bottom:24px;right:24px;padding:14px 20px;border-radius:10px;background:var(--card);border:1px solid var(--border);color:var(--text);font-size:13px;font-weight:500;box-shadow:0 8px 32px rgba(0,0,0,0.3);z-index:1000;transition:all 0.3s ease;border-left:3px solid " + (type==="success"?"#22c55e":"#ff2d20") + ";";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

function renderStats(data) {
  if (data.total_downloaded === 0) {
    document.getElementById("stats-content").style.display = "none";
    document.getElementById("stats-empty").style.display = "";
    return;
  }
  document.getElementById("stats-content").style.display = "";
  document.getElementById("stats-empty").style.display = "none";
  document.getElementById("stat-total").textContent = data.total_downloaded;
  document.getElementById("stat-rate").textContent = data.success_rate + "%";
  document.getElementById("stat-rate-label").textContent = data.total_success + " succeeded / " + data.total_failed + " failed";
  document.getElementById("stat-rate-bar").style.width = data.success_rate + "%";
  document.getElementById("stat-bytes").textContent = data.total_bytes_human;
  document.getElementById("stat-active").textContent = data.active_now;

  const chart = document.getElementById("daily-chart");
  chart.innerHTML = data.daily_bars.map(b =>
    '<div class="bar" style="height:' + b.pct + '%"><div class="bar-label">' + escapeHtml(b.label) + '</div></div>'
  ).join('');

  const breakdown = document.getElementById("status-breakdown");
  breakdown.innerHTML = data.status_breakdown.map(item =>
    '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">' +
    '<div style="width:12px;height:12px;border-radius:50%;background:' + item.color + ';"></div>' +
    '<div style="flex:1;font-size:13px;">' + escapeHtml(item.label) + '</div>' +
    '<div style="font-weight:700;">' + item.count + '</div>' +
    '<div style="font-size:12px;color:var(--text-secondary);width:50px;text-align:right;">' + item.pct + '%</div></div>' +
    '<div class="progress-bar" style="height:4px;margin-bottom:12px;">' +
    '<div class="progress-fill" style="width:' + item.pct + '%;background:' + item.color + ';"></div></div>'
  ).join('');
}

function fetchStats() {
  fetch("/api/stats").then(r => r.json()).then(renderStats).catch(() => {});
}

document.getElementById("reset-stats-btn").addEventListener("click", function() {
  if (!confirm("Delete ALL download records from the database? Files on disk will NOT be deleted. This cannot be undone.")) return;
  const btn = this;
  btn.textContent = "Clearing..."; btn.disabled = true;
  fetch("/api/stats/reset", {method: "POST"})
    .then(r => r.json()).then(d => { showToast("Cleared " + d.deleted_records + " records"); fetchStats(); btn.textContent = "Clear History"; btn.disabled = false; })
    .catch(e => { showToast("Error: " + e, "error"); btn.textContent = "Clear History"; btn.disabled = false; });
});

fetchStats();
setInterval(fetchStats, 5000);
