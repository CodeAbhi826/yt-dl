const API_URL = 'http://127.0.0.1:5000';
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');

async function checkConnection() {
  try {
    const res = await fetch(`${API_URL}/api/info`);
    if (res.ok) {
      statusDot.className = 'status-dot online';
      statusText.textContent = 'Connected';
    } else {
      throw new Error();
    }
  } catch {
    statusDot.className = 'status-dot offline';
    statusText.textContent = 'Disconnected';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  checkConnection();

  const result = await chrome.storage.local.get(['defaultQuality']);
  const current = result.defaultQuality || '720p';

  document.querySelectorAll('.quality-btn').forEach(btn => {
    if (btn.dataset.q === current) btn.classList.add('active');
    btn.addEventListener('click', async () => {
      document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await chrome.storage.local.set({ defaultQuality: btn.dataset.q });
    });
  });
});
