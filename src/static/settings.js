function saveSettings() {
  const cfg = {
    download_dir: document.getElementById("download_dir").value,
    concurrent_limit: parseInt(document.getElementById("concurrent_limit").value),
  };
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(cfg)})
    .then(r=>r.json()).then(d=> showToast("Settings saved"));
}
