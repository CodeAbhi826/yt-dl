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

function formatChip(j) { return j.quality === 'audio' ? 'mp3' : 'mp4'; }

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

function buildThumb(j, cls) {
  if (j.thumbnail) {
    return '<img src="' + escapeHtml(j.thumbnail) + '" class="' + cls + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="' + cls + '-placeholder" style="display:none;">YT</div>';
  }
  if (j.video_id && j.url && /youtube\.com|youtu\.be/.test(j.url)) {
    return '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="' + cls + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="' + cls + '-placeholder" style="display:none;">YT</div>';
  }
  return '<div class="' + cls + '-placeholder">YT</div>';
}

let allDownloads = [];
let dlOffset = 0;
const DL_PAGE_SIZE = 24;

async function loadDownloads() {
  const r = await fetch('/api/downloads?limit=' + DL_PAGE_SIZE + '&offset=' + dlOffset);
  const data = await r.json();
  if (dlOffset === 0) allDownloads = data.jobs;
  else allDownloads = allDownloads.concat(data.jobs);
  renderDownloads(allDownloads);
  document.getElementById('dl-load-more').style.display =
    data.total > allDownloads.length ? '' : 'none';
}

function renderDownloads(jobs) {
  const grid = document.getElementById('dl-grid');
  if (!jobs.length) {
    grid.innerHTML = '<div style="text-align:center;padding:80px 0;color:var(--text-muted);"><p style="font-size:16px;font-weight:500;color:var(--text);">No downloads yet</p><p style="margin-top:8px;">Completed downloads will appear here.</p></div>';
    return;
  }
  grid.innerHTML = '<div class="grid-3">' + jobs.map(buildDownloadCard).join('') + '</div>';
}

function buildDownloadCard(j) {
  const thumb = buildThumb(j, 'q-thumb');
  const sizeStr = j.file_size ? formatBytes(j.file_size) : '';
  const meta = [j.quality, formatChip(j), sizeStr, timeAgo(j.completed_at || j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card completed" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✓ Completed</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bar"></div>'
    + '<div class="q-bottom" style="justify-content:flex-end;gap:12px;">'
    + '<span class="q-cancel" onclick="openFolder(\'' + escapeHtml(j.id) + '\')">Open</span>'
    + '<span class="q-cancel" onclick="redownloadJob(\'' + escapeHtml(j.id) + '\')">Redownload</span>'
    + '<span class="q-cancel" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')" style="color:var(--accent-hover);">Delete</span>'
    + '</div></div></div>';
}

function openFolder(id) {
  const job = allDownloads.find(j => j.id === id);
  if (!job || !job.file_path) return;
  const dir = job.file_path.substring(0, job.file_path.lastIndexOf('/'));
  fetch('/api/open', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path: dir})})
    .then(r => r.json()).then(d => { if (d.error) showToast(d.error, 'error'); });
}

function redownloadJob(id) {
  const job = allDownloads.find(j => j.id === id);
  if (!job) return;
  fetch('/api/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url: job.url, quality: job.quality})})
    .then(r => r.json()).then(d => showToast('Re-added to queue'));
}

function deleteJob(id) {
  const job = allDownloads.find(j => j.id === id);
  const hasFile = job && job.file_path;
  const msg = hasFile ? 'Delete this download AND remove the file from disk?' : 'Delete this download record?';
  if (!confirm(msg)) return;
  fetch('/api/jobs/' + id, {method:'DELETE'}).then(r => r.json()).then(d => {
    showToast('Deleted');
    allDownloads = allDownloads.filter(j => j.id !== id);
    renderDownloads(allDownloads);
  });
}

function loadMoreDownloads() {
  dlOffset += DL_PAGE_SIZE;
  loadDownloads();
}

document.addEventListener("DOMContentLoaded", function() {
  loadDownloads();

  document.getElementById('dl-search').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    const filtered = allDownloads.filter(j =>
      (j.title || '').toLowerCase().includes(q) ||
      (j.url || '').toLowerCase().includes(q)
    );
    renderDownloads(filtered);
  });

  document.getElementById('dl-sort').addEventListener('change', function() {
    const sort = this.value;
    allDownloads.sort((a, b) => {
      switch(sort) {
        case 'newest': return new Date(b.completed_at || b.created_at) - new Date(a.completed_at || a.created_at);
        case 'oldest': return new Date(a.completed_at || a.created_at) - new Date(b.completed_at || b.created_at);
        case 'largest': return (b.file_size || 0) - (a.file_size || 0);
        case 'smallest': return (a.file_size || 0) - (b.file_size || 0);
        case 'title': return (a.title || '').localeCompare(b.title || '');
      }
    });
    renderDownloads(allDownloads);
  });
});
