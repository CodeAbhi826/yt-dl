const API_URL = 'http://127.0.0.1:5000';

document.addEventListener('DOMContentLoaded', async () => {
  const result = await chrome.storage.local.get(['defaultQuality']);
  const current = result.defaultQuality || '720p';

  // Quality selector
  document.querySelectorAll('.quality-btn').forEach(btn => {
    if (btn.dataset.q === current) btn.classList.add('active');
    btn.addEventListener('click', async () => {
      document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await chrome.storage.local.set({ defaultQuality: btn.dataset.q });
      setStatus('Default quality: ' + btn.dataset.q, '');
    });
  });

  setStatus('Default quality: ' + current, '');

  // Download this page
  document.getElementById('download-page').addEventListener('click', async () => {
    const btn = document.getElementById('download-page');
    btn.disabled = true;
    btn.textContent = 'Queuing...';

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const url = tab.url;
      if (!url || (!url.includes('youtube.com/watch') && !url.includes('youtu.be/'))) {
        setStatus('Not a YouTube video page', 'error');
        btn.disabled = false;
        btn.textContent = '⬇ Download this page';
        return;
      }

      const quality = (await chrome.storage.local.get(['defaultQuality'])).defaultQuality || '720p';
      const res = await fetch(`${API_URL}/api/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, quality })
      });

      if (!res.ok) throw new Error('Failed to queue');
      setStatus('Download queued!', 'success');
    } catch (err) {
      setStatus('Error: ' + (err.message || 'Daemon not running'), 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '⬇ Download this page';
    }
  });
});

function setStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status' + (type ? ' ' + type : '');
}
