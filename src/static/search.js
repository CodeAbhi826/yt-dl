function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}

function doSearch() {
  const params = new URLSearchParams();
  const q = document.getElementById("search-input").value.trim();
  if (q) params.append("q", q);
  const status = document.getElementById("status-filter").value;
  if (status) params.append("status", status);
  const quality = document.getElementById("quality-filter").value;
  if (quality) params.append("quality", quality);
  const date = document.getElementById("date-filter").value;
  if (date) params.append("date", date);
  fetch("/api/search?" + params.toString()).then(r => r.json()).then(data => renderResults(data));
}

function renderResults(jobs) {
  const tbody = document.getElementById("search-results");
  if (!jobs.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--text-secondary);">No results found</td></tr>';
    return;
  }
  tbody.innerHTML = jobs.map(j => '<tr><td><div style="display:flex;align-items:center;gap:12px;"><div style="width:48px;height:36px;background:var(--hover);border-radius:8px;overflow:hidden;">' + (j.video_id ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" style="width:100%;height:100%;object-fit:cover;">' : '') + '</div><div><div style="font-weight:600;font-size:13px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div><div style="font-size:11px;color:var(--text-secondary);">' + escapeHtml(j.url) + '</div></div></div></td><td><span class="tag">' + escapeHtml(j.quality) + '</span></td><td><span class="tag tag-' + escapeHtml(j.status) + '">' + escapeHtml(j.status) + '</span></td><td style="width:180px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;"><span>' + (j.progress || 0) + '%</span></div><div class="progress-bar"><div class="progress-fill" style="width:' + (j.progress || 0) + '%"></div></div></td><td style="font-size:12px;color:var(--text-secondary);">' + escapeHtml(j.created_at || '') + '</td><td><div style="display:flex;gap:4px;">' + (j.status === 'failed' ? '<button class="btn btn-secondary btn-sm" onclick="retryJob(\'' + escapeHtml(j.id) + '\')">Retry</button>' : '') + '<button class="btn btn-danger btn-sm" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</button></div></td></tr>').join('');
}

function retryJob(id) { fetch("/api/jobs/"+id+"/retry",{method:"POST"}).then(r=>r.json()).then(d=>{doSearch();}); }
function deleteJob(id) { if(!confirm("Delete?"))return; fetch("/api/jobs/"+id,{method:"DELETE"}).then(r=>r.json()).then(d=>{doSearch();}); }
