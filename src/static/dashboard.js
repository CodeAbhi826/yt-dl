function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function formatBytes(b) {
  if (!b || b === 0) return '';
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (b < 1024) return b.toFixed(1) + ' ' + unit;
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

const cards = new Map();
let currentFilter = 'all';
let selectedIds = new Set();

document.getElementById("filter-tabs").addEventListener("click", function(e) {
  const tab = e.target.closest(".filter-tab");
  if (!tab) return;
  document.querySelectorAll(".filter-tab").forEach(t => t.classList.remove("active"));
  tab.classList.add("active");
  currentFilter = tab.dataset.filter;
  applyFilter();
});

function applyFilter() {
  for (const [id, card] of cards) {
    card.style.display = (currentFilter === 'all' || card.dataset.status === currentFilter) ? '' : 'none';
  }
}

function createCard(j) {
  const card = document.createElement("div");
  card.className = "download-card";
  card.dataset.id = j.id;
  card.dataset.status = j.status;
  card.innerHTML = buildCardContent(j);
  card.addEventListener("click", function(e) {
    if (e.target.type === 'checkbox') return;
    const cb = this.querySelector(".card-check");
    if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
  });
  return card;
}

function buildCardContent(j) {
  const checked = selectedIds.has(j.id) ? 'checked' : '';
  const thumbnail = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" alt="" class="card-thumb">'
    : '<div class="card-thumb card-thumb-placeholder">YT</div>';

  const fileSize = (j.status === 'completed' && j.file_size)
    ? '<div class="card-file-size">' + formatBytes(j.file_size) + '</div>'
    : '';

  const openFolder = (j.status === 'completed' && j.file_path)
    ? '<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();openFolder(\'' + escapeHtml(j.id) + '\')">Open</button>'
    : '';

  const speedEta = (j.status === 'downloading' && (j.speed || j.eta))
    ? '<div class="card-speed-eta">' + escapeHtml(j.speed || '') + (j.speed && j.eta ? ' · ' : '') + escapeHtml(formatEta(j.eta)) + '</div>'
    : '';

  const errorMsg = (j.status === 'failed' && j.error_message)
    ? '<div class="card-error">' + escapeHtml(j.error_message) + '</div>'
    : '';

  let actions = '';
  if (j.status === 'failed') {
    actions += '<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();retryJob(\'' + escapeHtml(j.id) + '\')">Retry</button>';
    actions += '<button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</button>';
  } else if (j.status === 'downloading') {
    actions += '<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();cancelJob(\'' + escapeHtml(j.id) + '\')">Cancel</button>';
  } else if (j.status === 'queued') {
    actions += '<button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</button>';
  } else {
    actions += openFolder;
    actions += '<button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</button>';
  }

  const progress = j.progress || 0;
  const progressLabel = j.status === 'completed' ? '100%' : (progress + '%');

  return '<div class="card-checkbox"><input type="checkbox" class="card-check" value="' + escapeHtml(j.id) + '" ' + checked + ' onchange="event.stopPropagation();toggleRow(\'' + escapeHtml(j.id) + '\')"></div>'
    + '<div class="card-thumb-wrap">' + thumbnail + '</div>'
    + '<div class="card-body">'
    + '<div class="card-title-text" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div class="card-url">' + escapeHtml(j.url) + '</div>'
    + '<div class="card-meta">'
    + '<span class="tag">' + escapeHtml(j.quality) + '</span>'
    + '<span class="tag tag-' + escapeHtml(j.status) + '">' + escapeHtml(j.status) + '</span>'
    + '</div>'
    + '<div class="card-progress">'
    + '<div class="progress-bar"><div class="progress-fill" style="width:' + progress + '%"></div></div>'
    + '<div class="card-progress-info"><span>' + progressLabel + '</span>' + speedEta + '</div>'
    + '</div>'
    + fileSize
    + errorMsg
    + '<div class="card-actions">' + actions + '</div>'
    + '</div>';
}

function updateCard(card, j) {
  const oldStatus = card.dataset.status;
  card.dataset.status = j.status;
  card.innerHTML = buildCardContent(j);
  if (currentFilter !== 'all' && j.status !== currentFilter) {
    card.style.display = 'none';
  } else {
    card.style.display = '';
  }
}

function renderQueue(jobs) {
  const grid = document.getElementById("queue-grid");
  const newIds = new Set(jobs.map(j => j.id));

  for (const [id, card] of cards) {
    if (!newIds.has(id)) {
      card.remove();
      cards.delete(id);
    }
  }

  for (const j of jobs) {
    const existing = cards.get(j.id);
    if (existing) {
      updateCard(existing, j);
    } else {
      const card = createCard(j);
      cards.set(j.id, card);
      grid.appendChild(card);
      if (currentFilter !== 'all' && j.status !== currentFilter) {
        card.style.display = 'none';
      }
    }
  }

  updateBulkBar();

  if (jobs.length === 0) {
    grid.innerHTML = '<div class="empty-state"><div style="font-size:48px;margin-bottom:16px;opacity:0.3;">⬇</div><p style="font-size:16px;font-weight:600;">No downloads yet</p><p style="color:var(--text-secondary);">Paste a URL above or use the Brave extension.</p></div>';
    cards.clear();
  }
}

let sseReconnectTimer = null;

function connectSSE() {
  if (sseReconnectTimer) {
    clearTimeout(sseReconnectTimer);
    sseReconnectTimer = null;
  }

  const source = new EventSource("/api/queue/stream");

  source.onmessage = function(e) {
    if (e.data === ": unchanged") return;
    try {
      const data = JSON.parse(e.data);
      renderQueue(data);
    } catch (err) {}
  };

  source.onerror = function() {
    source.close();
    sseReconnectTimer = setTimeout(connectSSE, 3000);
  };
}

function openFolder(jobId) {
  fetch("/api/jobs/" + jobId).then(r => r.json()).then(j => {
    if (j.file_path) {
      const dir = j.file_path.substring(0, j.file_path.lastIndexOf('/'));
      fetch("/api/open", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path: dir})});
    }
  });
}

function toggleRow(id) {
  if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
  const card = cards.get(id);
  if (card) {
    const cb = card.querySelector(".card-check");
    if (cb) cb.checked = selectedIds.has(id);
  }
  updateBulkBar();
}

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar"), count = document.getElementById("bulk-count");
  if (selectedIds.size > 0) { bar.classList.remove("hidden"); count.textContent = selectedIds.size + " selected"; }
  else { bar.classList.add("hidden"); }
}

function clearSelection() {
  selectedIds.clear();
  for (const [, card] of cards) {
    const cb = card.querySelector(".card-check");
    if (cb) cb.checked = false;
  }
  updateBulkBar();
}

function bulkDelete() {
  if (!confirm("Delete " + selectedIds.size + " items?")) return;
  fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids:Array.from(selectedIds)})})
    .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " items"); clearSelection(); });
}

function bulkRetry() {
  fetch("/api/bulk/retry", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids:Array.from(selectedIds)})})
    .then(r => r.json()).then(d => { showToast("Retried " + d.retried + " items"); clearSelection(); });
}

function retryJob(id) {
  fetch("/api/jobs/" + id + "/retry", {method:"POST"}).then(r => r.json()).then(d => { showToast("Job retried"); });
}

function deleteJob(id) {
  if (!confirm("Delete this item?")) return;
  fetch("/api/jobs/" + id, {method:"DELETE"}).then(r => r.json()).then(d => { showToast("Deleted"); });
}

function cancelJob(id) {
  fetch("/api/jobs/" + id + "/cancel", {method:"POST"}).then(r => r.json()).then(d => { showToast("Cancelled"); });
}

connectSSE();