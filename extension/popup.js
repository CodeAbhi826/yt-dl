const API_URL = 'http://127.0.0.1:5000';
const dot = document.getElementById('statusDot');
const text = document.getElementById('statusText');

async function applyTheme() {
  try {
    const cached = await chrome.storage.local.get(['theme']);
    if (cached.theme) {
      document.documentElement.setAttribute('data-theme', cached.theme === 'light' ? 'light' : '');
    }
    const r = await fetch(`${API_URL}/api/settings`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      const data = await r.json();
      const theme = data.theme || 'dark';
      document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : '');
      await chrome.storage.local.set({ theme });
    }
  } catch {}
}

async function checkConnection() {
  try {
    const r = await fetch(`${API_URL}/api/info`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      dot.style.background = '#22c55e';
      text.textContent = 'Connected';
    } else {
      throw new Error();
    }
  } catch {
    dot.style.background = '#ef4444';
    text.textContent = 'Disconnected';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  applyTheme();
  checkConnection();
  setInterval(checkConnection, 10000);

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

  const { apiKey } = await chrome.storage.local.get('apiKey');
  const keyInput = document.getElementById('apiKey');
  if (keyInput) {
    if (apiKey) keyInput.value = apiKey;
    keyInput.addEventListener('input', async () => {
      await chrome.storage.local.set({ apiKey: keyInput.value });
    });
  }
});