#!/usr/bin/env python3
"""HTML templates for yt-dl dashboard."""

CSS_COMMON = """
:root { --bg: #0a0a0a; --card: #141414; --hover: #1a1a1a; --border: #2a2a2a; --text: #e5e5e5; --text-secondary: #888888; --accent: #ff2d20; --accent-hover: #e0261a; --green: #22c55e; --orange: #f39c12; --gray: #666666; --radius-card: 16px; --radius-btn: 10px; --font: 'Inter', sans-serif; }
[data-theme="light"] { --bg: #f5f5f5; --card: #ffffff; --hover: #f0f0f0; --border: #e0e0e0; --text: #1a1a1a; --text-secondary: #666666; --accent: #ff2d20; --accent-hover: #e0261a; --green: #16a34a; --orange: #d97706; --gray: #9ca3af; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 24px; }
.nav { display: flex; align-items: center; gap: 8px; padding: 16px 24px; background: var(--card); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 100; }
.nav-brand { font-size: 20px; font-weight: 700; color: var(--accent); text-decoration: none; }
.nav-links { display: flex; gap: 4px; margin-left: 32px; flex: 1; }
.nav-link { padding: 8px 16px; border-radius: var(--radius-btn); text-decoration: none; color: var(--text-secondary); font-size: 13px; font-weight: 500; }
.nav-link:hover, .nav-link.active { color: var(--text); background: var(--hover); }
.btn { padding: 8px 16px; border-radius: var(--radius-btn); border: none; cursor: pointer; font-family: var(--font); font-size: 13px; font-weight: 500; }
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: var(--hover); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { background: var(--border); }
.btn-danger { background: #dc2626; color: white; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.card { background: var(--card); border-radius: var(--radius-card); border: 1px solid var(--border); padding: 24px; }
.card-title { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); margin-bottom: 16px; font-weight: 600; }
.grid { display: grid; gap: 20px; }
.grid-2 { grid-template-columns: repeat(2, 1fr); }
.grid-4 { grid-template-columns: repeat(4, 1fr); }
@media (max-width: 1024px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .grid-2, .grid-4 { grid-template-columns: 1fr; } }
.stat-value { font-size: 32px; font-weight: 700; color: var(--text); }
.stat-label { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.progress-bar { height: 6px; background: var(--hover); border-radius: 3px; overflow: hidden; margin-top: 12px; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s ease; }
.form-group { margin-bottom: 20px; }
.form-label { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); margin-bottom: 8px; font-weight: 600; }
.form-input, .form-select { width: 100%; padding: 12px 16px; border-radius: var(--radius-btn); border: 1px solid var(--border); background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; }
.form-input:focus, .form-select:focus { outline: none; border-color: var(--accent); }
.checkbox-group { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.checkbox-group input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--accent); }
.tag { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
.tag-queued { background: rgba(102,102,102,0.2); color: var(--gray); }
.tag-downloading { background: rgba(255,45,32,0.15); color: var(--accent); }
.tag-completed { background: rgba(34,197,94,0.15); color: var(--green); }
.tag-failed { background: rgba(220,38,38,0.15); color: #dc2626; }
.tag-cancelled { background: rgba(243,156,18,0.15); color: var(--orange); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 12px 16px; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); font-weight: 600; border-bottom: 1px solid var(--border); }
td { padding: 14px 16px; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--hover); }
.toast { position: fixed; bottom: 24px; right: 24px; padding: 14px 20px; border-radius: var(--radius-btn); background: var(--card); border: 1px solid var(--border); color: var(--text); font-size: 13px; font-weight: 500; box-shadow: 0 8px 32px rgba(0,0,0,0.3); z-index: 1000; transform: translateY(100px); opacity: 0; transition: all 0.3s ease; }
.toast.show { transform: translateY(0); opacity: 1; }
.toast.success { border-left: 3px solid var(--green); }
.toast.error { border-left: 3px solid var(--accent); }
.bulk-bar { display: flex; gap: 8px; align-items: center; padding: 12px 16px; background: var(--hover); border-radius: var(--radius-btn); margin-bottom: 16px; border: 1px solid var(--border); }
.bulk-bar.hidden { display: none; }
.theme-toggle { background: var(--hover); border: 1px solid var(--border); color: var(--text); cursor: pointer; padding: 8px; border-radius: 10px; }
.theme-toggle:hover { background: var(--border); }
.spinner { width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
"""

NAV = """
<nav class="nav">
<a href="/" class="nav-brand">yt-dl</a>
<div class="nav-links">
<a href="/" class="nav-link {{ 'active' if active == 'dashboard' else '' }}">Queue</a>
<a href="/stats" class="nav-link {{ 'active' if active == 'stats' else '' }}">Stats</a>
<a href="/logs" class="nav-link {{ 'active' if active == 'logs' else '' }}">Logs</a>
<a href="/search" class="nav-link {{ 'active' if active == 'search' else '' }}">Search</a>
<a href="/settings" class="nav-link {{ 'active' if active == 'settings' else '' }}">Settings</a>
</div>
<div class="nav-actions">
<button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
</button>
</div>
</nav>
"""

THEME_JS = """
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
"""

TOAST_JS = """
function showToast(message, type="success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = "toast show " + type;
  setTimeout(() => toast.classList.remove("show"), 3000);
}
"""

DASHBOARD_HTML = f"""
<!DOCTYPE html>
<html lang="en" data-theme="{{{{ theme }}}}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Queue - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
{CSS_COMMON}
</style>
</head>
<body>
{NAV}
<main class="container">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px;">
<h1 style="font-size:24px; font-weight:700;">Download Queue</h1>
<div style="display:flex; gap:8px;">
<button class="btn btn-secondary btn-sm" onclick="refreshQueue()">Refresh</button>
</div>
</div>
<div id="bulk-bar" class="bulk-bar hidden">
<span id="bulk-count" style="font-size:13px; font-weight:600;">0 selected</span>
<div style="flex:1"></div>
<button class="btn btn-secondary btn-sm" onclick="bulkRetry()">Retry</button>
<button class="btn btn-danger btn-sm" onclick="bulkDelete()">Delete</button>
<button class="btn btn-secondary btn-sm" onclick="clearSelection()">Cancel</button>
</div>
<div class="card">
<div style="overflow-x:auto;">
<table>
<thead><tr>
<th style="width:32px"><input type="checkbox" id="select-all" onchange="toggleSelectAll()"></th>
<th>Video</th><th>Quality</th><th>Status</th><th>Progress</th><th>Added</th><th style="width:120px">Actions</th>
</tr></thead>
<tbody id="queue-body">
<tr><td colspan="7" style="text-align:center; padding:40px;"><div class="spinner" style="margin:0 auto;"></div></td></tr>
</tbody>
</table>
</div>
</div>
</main>
<div id="toast" class="toast"></div>
<script>
{THEME_JS}
{TOAST_JS}
let selectedIds = new Set();
function refreshQueue() {{
  fetch("/api/queue").then(r=>r.json()).then(data=>renderQueue(data));
}}
function renderQueue(jobs) {{
  const tbody = document.getElementById("queue-body");
  if (!jobs.length) {{
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:40px;"><p style="font-size:16px;font-weight:600;">No downloads yet</p><p style="color:var(--text-secondary);">Use the Brave extension to add videos.</p></td></tr>';
    return;
  }}
  tbody.innerHTML = jobs.map(j => `<tr data-id="${{j.id}}"><td><input type="checkbox" class="row-check" value="${{j.id}}" onchange="toggleRow('${{j.id}}')"></td><td><div style="display:flex;align-items:center;gap:12px;"><div style="width:48px;height:36px;background:var(--hover);border-radius:8px;overflow:hidden;">${{j.video_id ? `<img src="https://i.ytimg.com/vi/${{j.video_id}}/mqdefault.jpg" style="width:100%;height:100%;object-fit:cover;">` : 'YT'}}</div><div><div style="font-weight:600;font-size:13px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${{j.title || j.video_id || 'Unknown'}}</div><div style="font-size:11px;color:var(--text-secondary);">${{j.url}}</div></div></div></td><td><span class="tag">${{j.quality}}</span></td><td><span class="tag tag-${{j.status}}">${{j.status}}</span></td><td style="width:180px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;"><span>${{j.progress || 0}}%</span><span style="color:var(--text-secondary);">${{j.speed || ''}}</span></div><div class="progress-bar"><div class="progress-fill" style="width:${{j.progress || 0}}%"></div></div></td><td style="font-size:12px;color:var(--text-secondary);">${{j.created_at || ''}}</td><td><div style="display:flex;gap:4px;">${{j.status === 'failed' ? `<button class="btn btn-secondary btn-sm" onclick="retryJob('${{j.id}}')">Retry</button>` : ''}}${{j.status !== 'downloading' ? `<button class="btn btn-danger btn-sm" onclick="deleteJob('${{j.id}}')">Delete</button>` : `<button class="btn btn-secondary btn-sm" onclick="cancelJob('${{j.id}}')">Cancel</button>`}}</div></td></tr>`).join('');
  updateBulkBar();
}}
function toggleRow(id) {{ if(selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id); updateBulkBar(); }}
function toggleSelectAll() {{ const all=document.getElementById("select-all").checked; document.querySelectorAll(".row-check").forEach(cb=>{{cb.checked=all; if(all) selectedIds.add(cb.value); else selectedIds.delete(cb.value);}}); updateBulkBar(); }}
function updateBulkBar() {{ const bar=document.getElementById("bulk-bar"), count=document.getElementById("bulk-count"); if(selectedIds.size>0){{bar.classList.remove("hidden"); count.textContent=selectedIds.size+" selected";
}}else{{bar.classList.add("hidden");
}} }}
function clearSelection() {{ selectedIds.clear(); document.querySelectorAll(".row-check,#select-all").forEach(cb=>cb.checked=false); updateBulkBar(); }}
function bulkDelete() {{ if(!confirm("Delete "+selectedIds.size+" items?"))return; fetch("/api/bulk/delete",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{ids:Array.from(selectedIds)}})}}).then(r=>r.json()).then(d=>{{showToast("Deleted "+d.deleted+" items"); clearSelection(); refreshQueue();}}); }}
function bulkRetry() {{ fetch("/api/bulk/retry",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{ids:Array.from(selectedIds)}})}}).then(r=>r.json()).then(d=>{{showToast("Retried "+d.retried+" items"); clearSelection(); refreshQueue();}}); }}
function retryJob(id) {{ fetch("/api/jobs/"+id+"/retry",{{method:"POST"}}).then(r=>r.json()).then(d=>{{showToast("Job retried"); refreshQueue();}}); }}
function deleteJob(id) {{ if(!confirm("Delete this item?"))return; fetch("/api/jobs/"+id,{{method:"DELETE"}}).then(r=>r.json()).then(d=>{{showToast("Deleted"); refreshQueue();}}); }}
function cancelJob(id) {{ fetch("/api/jobs/"+id+"/cancel",{{method:"POST"}}).then(r=>r.json()).then(d=>{{showToast("Cancelled"); refreshQueue();}}); }}
refreshQueue(); setInterval(refreshQueue, 3000);
</script>
</body>
</html>
"""

# Note: Using f-string with escaped braces for Jinja2 compatibility
# The actual templates use {{ }} for Jinja2 variables
