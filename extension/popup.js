document.addEventListener('DOMContentLoaded', async () => {
  const result = await chrome.storage.local.get(['defaultQuality']);
  const current = result.defaultQuality || '720p';

  document.querySelectorAll('.quality-btn').forEach(btn => {
    if (btn.dataset.q === current) btn.classList.add('active');
    btn.addEventListener('click', async () => {
      document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await chrome.storage.local.set({ defaultQuality: btn.dataset.q });
      document.getElementById('status').textContent = 'Default quality: ' + btn.dataset.q;
    });
  });

  document.getElementById('status').textContent = 'Default quality: ' + current;
});
