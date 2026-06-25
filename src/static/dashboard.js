function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function formatBytes(b) {
  if (!b || b === 0) return '0 B';
  for (const u of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (b < 1024) return b.toFixed(1) + ' ' + u;
    b /= 1024;
  }
  return b.toFixed(1) + ' PB';
}

function formatEta(eta) {
  if (!eta || eta === 'N/A') return '';
  if (eta.includes(':')) return eta;
  const s = parseInt(eta);
  if (isNaN(s)) return '';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
  return m + ':' + String(sec).padStart(2, '0');
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const now = Date.now();
  const d = new Date(dateStr + (dateStr.includes('Z') || dateStr.includes('+') ? '' : 'Z'));
  const diff = Math.floor((now - d.getTime()) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

const $ = id => document.getElementById(id);

function buildDownloadingCard(j) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="dl-thumb">'
    : '<div class="dl-thumb-placeholder">YT</div>';
  const pct = j.progress || 0;
  const speed = j.speed ? escapeHtml(j.speed) : '';
  const eta = formatEta(j.eta);
  const sizeInfo = j.file_size ? formatBytes(j.file_size) : '';
  const statsParts = [];
  statsParts.push('<span class="dl-stats-pct">' + pct.toFixed(1) + '%</span>');
  if (sizeInfo) statsParts.push(sizeInfo);
  if (speed) statsParts.push(speed);
  if (eta) statsParts.push(eta + ' left');
  const statsStr = statsParts.join(' • ');

  return '<div class="dl-card" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="dl-thumb-wrap">' + thumb + '</div>'
    + '<div class="dl-body">'
    + '<div><div class="dl-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div class="dl-meta"><span class="dl-chip">' + escapeHtml(j.quality) + '</span><span class="dl-chip">mp4</span></div></div>'
    + '<div>'
    + '<div class="dl-progress"><div class="dl-progress-bar"><div class="dl-progress-fill" style="width:' + pct + '%"></div></div></div>'
    + '<div class="dl-stats"><span>' + statsStr + '</span><span class="dl-cancel" onclick="cancelJob(\'' + escapeHtml(j.id) + '\')">Cancel</span></div>'
    + '</div></div></div>';
}

function buildQueueCard(j, pos) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="q-thumb">'
    : '<div class="q-thumb-placeholder">YT</div>';
  const sizeInfo = j.file_size ? formatBytes(j.file_size) : '';
  const meta = [j.quality, 'mp4', sizeInfo].filter(Boolean).join(' • ');
  const eta = formatEta(j.eta);
  const etaHtml = eta ? '<span class="q-eta">🕒 ~' + escapeHtml(eta) + '</span>' : '';

  return '<div class="q-card" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div class="q-position">#' + pos + '</div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bottom">' + etaHtml + '<span class="q-cancel" onclick="cancelJob(\'' + escapeHtml(j.id) + '\')">Cancel</span></div>'
    + '</div></div>';
}

function buildRecentCard(j) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="q-thumb">'
    : '<div class="q-thumb-placeholder">YT</div>';
  const sizeInfo = j.file_size ? formatBytes(j.file_size) : '';
  const meta = [j.quality, 'mp4', sizeInfo, timeAgo(j.completed_at || j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card completed" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✓ COMPLETED</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bar"></div>'
    + '<div class="q-bottom" style="justify-content:flex-end;"><span class="q-cancel" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</span></div>'
    + '</div></div>';
}

function buildFailedCard(j) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="q-thumb">'
    : '<div class="q-thumb-placeholder">YT</div>';
  const meta = [j.quality, 'mp4', 'FAILED', timeAgo(j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card failed" data-id="' + escapeHtml(j.id) + '" style="cursor:pointer" onclick="window.location.href=\'/logs\'">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✕ FAILED</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bottom" style="justify-content:flex-end;"><span class="q-retry" onclick="event.stopPropagation();retryJob(\'' + escapeHtml(j.id) + '\')">Retry</span></div>'
    + '</div></div>';
}

function buildSection(label, gridClass, cardsHtml) {
  if (!cardsHtml) return '';
  const count = (cardsHtml.match(/data-id=/g) || []).length;
  return '<div class="section"><div class="section-label">' + escapeHtml(label) + ' (' + count + ')</div><div class="' + gridClass + '">' + cardsHtml + '</div></div>';
}

let prevJobs = [];

function renderDashboard(jobs) {
  prevJobs = jobs;
  const sections = document.getElementById("sections");
  if (!jobs.length) {
    sections.innerHTML = '<div class="section" style="text-align:center;padding:80px 0;color:var(--text-muted);"><p style="font-size:16px;font-weight:600;color:var(--text);">No downloads</p><p style="margin-top:8px;">Add videos using the Brave extension.</p></div>';
    updateFooter(jobs);
    return;
  }

  const downloading = jobs.filter(j => j.status === 'downloading');
  const queued = jobs.filter(j => j.status === 'queued');
  const failed = jobs.filter(j => j.status === 'failed');
  const completed = jobs.filter(j => j.status === 'completed').slice(0, 6);

  let html = '';

  if (downloading.length) {
    html += buildSection('DOWNLOADING', 'grid-2', downloading.map(buildDownloadingCard).join(''));
  }
  if (queued.length) {
    html += buildSection('QUEUED', 'grid-3', queued.map((j, i) => buildQueueCard(j, i + 1)).join(''));
  }
  if (failed.length) {
    html += buildSection('FAILED', 'grid-3', failed.map(buildFailedCard).join(''));
  }
  if (completed.length) {
    html += buildSection('RECENT', 'grid-3', completed.map(buildRecentCard).join(''));
  }

  sections.innerHTML = html;
  updateFooter(jobs);
}

function updateFooter(jobs) {
  const active = jobs.filter(j => j.status === 'downloading').length;
  const queued = jobs.filter(j => j.status === 'queued').length;
  const left = document.getElementById("footer-left");
  if (left) left.textContent = '⬇ ' + active + ' active • ' + queued + ' queued';
}

function retryJob(id) {
  fetch("/api/jobs/" + id + "/retry", {method:"POST"}).then(r => r.json()).then(d => { showToast("Retried"); });
}

function cancelJob(id) {
  fetch("/api/jobs/" + id + "/cancel", {method:"POST"}).then(r => r.json()).then(d => { showToast("Cancelled"); });
}

function deleteJob(id) {
  if (!confirm("Delete?")) return;
  fetch("/api/jobs/" + id, {method:"DELETE"}).then(r => r.json()).then(d => { showToast("Deleted"); });
}

// Footer actions
document.addEventListener("DOMContentLoaded", function() {
  const clearBtn = document.getElementById("clear-completed");
  if (clearBtn) {
    clearBtn.addEventListener("click", function() {
      const completed = prevJobs.filter(j => j.status === 'completed');
      if (!completed.length) return;
      fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
        .then(r => r.json()).then(d => { showToast("Cleared " + d.deleted + " completed"); });
    });
  }

  const pauseBtn = document.getElementById("pause-all");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", function() {
      const dl = prevJobs.filter(j => j.status === 'downloading');
      if (!dl.length) return;
      dl.forEach(j => cancelJob(j.id));
      showToast("Cancelled " + dl.length + " active");
    });
  }
});

// SSE
let sseTimer = null;
function connectSSE() {
  if (sseTimer) { clearTimeout(sseTimer); sseTimer = null; }
  const source = new EventSource("/api/queue/stream");
  source.onmessage = function(e) {
    if (e.data === ": unchanged") return;
    try { renderDashboard(JSON.parse(e.data)); } catch (err) {}
  };
  source.onerror = function() { source.close(); sseTimer = setTimeout(connectSSE, 3000); };
}

connectSSE();