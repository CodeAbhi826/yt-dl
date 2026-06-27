function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function thumbHtml(j, cls) {
  if (!j.video_id || !/youtube\.com|youtu\.be/.test(j.url || '')) {
    return '<div class="' + cls + '-placeholder">YT</div>';
  }
  return '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="' + cls + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="' + cls + '-placeholder" style="display:none;">YT</div>';
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
  const thumb = thumbHtml(j, 'dl-thumb');
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
  const thumb = thumbHtml(j, 'q-thumb');
  const sizeInfo = j.file_size ? formatBytes(j.file_size) : '';
  const meta = [j.quality, 'mp4', sizeInfo, downloadsEnabled ? '' : '⏸ waiting for toggle'].filter(Boolean).join(' • ');
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
  const thumb = thumbHtml(j, 'q-thumb');
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
  const thumb = thumbHtml(j, 'q-thumb');
  const errMsg = j.error_message || 'Unknown error';
  const meta = [j.quality, 'mp4', errMsg, timeAgo(j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card failed" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✕ FAILED</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bottom" style="justify-content:space-between;"><span class="q-retry" onclick="retryJob(\'' + escapeHtml(j.id) + '\')">Retry</span><span class="q-cancel" style="cursor:pointer;" onclick="window.location.href=\'/logs\'">View logs →</span></div>'
    + '</div></div>';
}

function buildPausedCard(j) {
  const thumb = thumbHtml(j, 'dl-thumb');
  const pct = j.progress || 0;
  return '<div class="dl-card" data-id="' + escapeHtml(j.id) + '" style="opacity:0.6">'
    + '<div class="dl-thumb-wrap">' + thumb + '</div>'
    + '<div class="dl-body">'
    + '<div><div class="dl-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div class="dl-meta"><span class="dl-chip">⏸ Paused</span><span class="dl-chip">' + pct.toFixed(1) + '%</span></div></div>'
    + '<div><div class="dl-progress"><div class="dl-progress-bar"><div class="dl-progress-fill" style="width:' + pct + '%"></div></div></div>'
    + '<div class="dl-stats"><span>Paused at ' + pct.toFixed(1) + '%</span><span class="dl-cancel" onclick="resumeJob(\'' + escapeHtml(j.id) + '\')">Resume</span></div></div>'
    + '</div></div>';
}

function resumeJob(id) {
  fetch("/api/jobs/" + id + "/resume", {method:"POST"})
    .then(r => r.json())
    .then(d => { showToast("Resumed"); });
}

function buildSection(label, gridClass, cardsHtml) {
  if (!cardsHtml) return '';
  const count = (cardsHtml.match(/data-id=/g) || []).length;
  return '<div class="section"><div class="section-label">' + escapeHtml(label) + ' (' + count + ')</div><div class="' + gridClass + '">' + cardsHtml + '</div></div>';
}

let prevJobs = [];
let currentOffset = 0;
const PAGE_SIZE = 200;

async function loadMore() {
  currentOffset += PAGE_SIZE;
  const r = await fetch(`/api/queue?limit=${PAGE_SIZE}&offset=${currentOffset}`);
  const olderJobs = await r.json();
  if (olderJobs.length === 0) {
    document.getElementById("load-more").style.display = "none";
    return;
  }
  prevJobs = prevJobs.concat(olderJobs);
  renderDashboard(prevJobs);
}

function renderDashboard(jobs) {
  prevJobs = jobs;
  const loadMoreBtn = document.getElementById("load-more");
  if (loadMoreBtn) loadMoreBtn.style.display = jobs.length >= PAGE_SIZE ? "" : "none";
  const sections = document.getElementById("sections");
  if (!jobs.length) {
    sections.innerHTML = '<div class="section" style="text-align:center;padding:80px 0;color:var(--text-muted);"><p style="font-size:16px;font-weight:600;color:var(--text);">No downloads</p><p style="margin-top:8px;">Add videos using the Brave extension.</p></div>';
    updateFooter(jobs);
    return;
  }

  const downloading = jobs.filter(j => j.status === 'downloading');
  const paused = jobs.filter(j => j.status === 'paused');
  const queued = jobs.filter(j => j.status === 'queued');
  const failed = jobs.filter(j => j.status === 'failed');
  const completed = jobs.filter(j => j.status === 'completed').slice(0, 6);

  let html = '';

  if (downloading.length) {
    html += buildSection('DOWNLOADING', 'grid-2', downloading.map(buildDownloadingCard).join(''));
  }
  if (paused.length) {
    html += buildSection('PAUSED', 'grid-2', paused.map(buildPausedCard).join(''));
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
  const paused = jobs.filter(j => j.status === 'paused').length;
  const left = document.getElementById("footer-left");
  if (left) left.textContent = '⬇ ' + active + ' active • ' + queued + ' queued' + (paused ? ' • ' + paused + ' paused' : '');

  const pauseBtn = document.getElementById("pause-all");
  if (pauseBtn) {
    if (active > 0) {
      pauseBtn.textContent = 'Pause all';
      pauseBtn.classList.add('footer-action-danger');
    } else if (paused > 0) {
      pauseBtn.textContent = 'Resume all';
      pauseBtn.classList.remove('footer-action-danger');
    } else {
      pauseBtn.textContent = 'Pause all';
      pauseBtn.classList.add('footer-action-danger');
    }
  }
}

function retryJob(id) {
  fetch("/api/jobs/" + id + "/retry", {method:"POST"}).then(r => r.json()).then(d => { showToast("Retried"); });
}

function cancelJob(id) {
  fetch("/api/jobs/" + id + "/cancel", {method:"POST"}).then(r => r.json()).then(d => { showToast("Cancelled"); });
}

function deleteJob(id) {
  const job = prevJobs.find(j => j.id === id);
  const hasFile = job && job.file_path && job.status === 'completed';
  const msg = hasFile
    ? "Delete this download AND remove the file from disk?"
    : "Delete this download record?";
  if (!confirm(msg)) return;
  fetch("/api/jobs/" + id, {method:"DELETE"}).then(r => r.json()).then(d => { showToast("Deleted"); });
}

// ─── Master Toggle ───
let downloadsEnabled = true;

async function loadToggleState() {
  try {
    const r = await fetch('/api/info');
    const d = await r.json();
    downloadsEnabled = d.downloads_enabled !== false;
    updateToggleUI();
  } catch (e) {
    downloadsEnabled = true;
    updateToggleUI();
  }
}

function updateToggleUI() {
  const toggle = document.getElementById('master-toggle');
  const label = document.getElementById('master-toggle-label');
  if (!toggle || !label) return;
  if (downloadsEnabled) {
    toggle.classList.add('on');
    label.classList.add('on');
    label.classList.remove('off');
    label.textContent = 'Downloads: ON';
  } else {
    toggle.classList.remove('on');
    label.classList.add('off');
    label.classList.remove('on');
    label.textContent = 'Downloads: OFF';
  }
}

async function toggleDownloads() {
  const newState = !downloadsEnabled;
  try {
    const r = await fetch('/api/toggle', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: newState })
    });
    if (!r.ok) throw new Error('Toggle failed');
    downloadsEnabled = newState;
    updateToggleUI();
    showToast(newState ? 'Downloads enabled' : 'Downloads paused — new jobs will wait in queue');
  } catch (e) {
    showToast('Toggle failed: ' + e.message, 'error');
  }
}

// ─── Bulk Add Modal ───
function openBulkModal() {
  document.getElementById('bulk-modal').style.display = 'flex';
  document.getElementById('bulk-urls').focus();
  updateBulkCount();
}

function closeBulkModal() {
  document.getElementById('bulk-modal').style.display = 'none';
}

function updateBulkCount() {
  const text = document.getElementById('bulk-urls').value;
  const urls = text.split('\n').map(s => s.trim()).filter(s => s.length > 0);
  const count = urls.length;
  const valid = urls.filter(u => /^https?:\/\//.test(u)).length;
  const invalid = count - valid;
  const el = document.getElementById('bulk-count');
  const submitBtn = document.getElementById('bulk-submit');
  if (count === 0) {
    el.textContent = '0 URLs detected';
    el.style.color = 'var(--text-muted)';
    if (submitBtn) submitBtn.textContent = 'Queue all';
  } else {
    el.textContent = `${count} URL${count !== 1 ? 's' : ''} detected` +
      (invalid > 0 ? ` · ${invalid} invalid` : '') +
      ` · duplicates auto-skipped on submit`;
    el.style.color = invalid > 0 ? 'var(--orange)' : 'var(--text-secondary)';
    if (submitBtn) submitBtn.textContent = `Queue all ${count}`;
  }
}

async function submitBulkAdd() {
  const text = document.getElementById('bulk-urls').value;
  const urls = text.split('\n').map(s => s.trim()).filter(s => s.length > 0);
  const quality = document.getElementById('bulk-quality').value;

  if (urls.length === 0) {
    showToast('No URLs to add', 'error');
    return;
  }

  const btn = document.getElementById('bulk-submit');
  btn.disabled = true;
  btn.textContent = 'Queuing...';

  try {
    const r = await fetch('/api/bulk/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, quality })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Bulk add failed');

    let msg = `Queued ${d.added} download${d.added !== 1 ? 's' : ''}`;
    if (d.skipped_duplicate > 0) msg += ` · ${d.skipped_duplicate} duplicate${d.skipped_duplicate !== 1 ? 's' : ''} skipped`;
    if (d.skipped_invalid > 0) msg += ` · ${d.skipped_invalid} invalid`;
    showToast(msg);

    if (d.added > 0) {
      closeBulkModal();
      document.getElementById('bulk-urls').value = '';
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Queue all';
  }
}

// Footer actions
document.addEventListener("DOMContentLoaded", function() {
  loadToggleState();
  const toggleRow = document.getElementById('master-toggle-row');
  if (toggleRow) {
    toggleRow.addEventListener('click', toggleDownloads);
  }
  // Re-check toggle state every 10s (in case another client changed it)
  setInterval(loadToggleState, 10000);

  const bulkBtn = document.getElementById('bulk-add');
  if (bulkBtn) {
    bulkBtn.addEventListener('click', openBulkModal);
  }
  const bulkTextarea = document.getElementById('bulk-urls');
  if (bulkTextarea) {
    bulkTextarea.addEventListener('input', updateBulkCount);
  }
  const modal = document.getElementById('bulk-modal');
  if (modal) {
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeBulkModal();
    });
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && modal && modal.style.display !== 'none') {
      closeBulkModal();
    }
  });

  const clearBtn = document.getElementById("clear-completed");
  if (clearBtn) {
    clearBtn.addEventListener("click", function() {
      const completed = prevJobs.filter(j => j.status === 'completed');
      if (!completed.length) return;
      if (!confirm("Delete " + completed.length + " completed download(s) AND their files from disk?")) return;
      fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
        .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " files"); });
    });
  }

  const pauseBtn = document.getElementById("pause-all");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", function() {
      const dl = prevJobs.filter(j => j.status === 'downloading');
      const paused = prevJobs.filter(j => j.status === 'paused');

      if (dl.length > 0) {
        if (!confirm("Pause " + dl.length + " active download(s)?")) return;
        fetch("/api/jobs/pause-all", {method:"POST"})
          .then(r => r.json())
          .then(d => showToast("Paused " + d.paused + " jobs"))
          .catch(() => showToast("Pause failed", "error"));
      } else if (paused.length > 0) {
        if (!confirm("Resume " + paused.length + " paused download(s)?")) return;
        fetch("/api/jobs/resume-all", {method:"POST"})
          .then(r => r.json())
          .then(d => showToast("Resumed " + d.resumed + " jobs"))
          .catch(() => showToast("Resume failed", "error"));
      }
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