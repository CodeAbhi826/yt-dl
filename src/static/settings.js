function saveSettings() {
  const cfg = {
    download_dir: document.getElementById("download_dir").value,
    default_quality: document.getElementById("default_quality").value,
    concurrent_limit: parseInt(document.getElementById("concurrent_limit").value),
    embed_metadata: document.getElementById("embed_metadata").checked,
    embed_thumbnail: document.getElementById("embed_thumbnail").checked,
    embed_chapters: document.getElementById("embed_chapters").checked,
    embed_subs: document.getElementById("embed_subs").checked,
  };
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(cfg)})
    .then(r=>r.json()).then(d=> showToast("Settings saved"));
}
