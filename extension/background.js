// yt-dl Extension Background Script
const API_URL = 'http://127.0.0.1:5000';
const DEFAULT_QUALITY = '720p';

// Notification polling state
let prevJobs = {};
let pollInterval = null;
let heartbeatInterval = null;
let dbusAvailable = false;

// ─── Context Menu ───────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'yt-dl-download',
    title: 'Download with yt-dl',
    contexts: ['link'],
    targetUrlPatterns: [
      '*://*.youtube.com/watch*',
      '*://*.youtube.com/shorts*',
      '*://youtu.be/*',
      '*://*.youtube.com/embed/*'
    ]
  });
  initNotificationSystem();
});

chrome.runtime.onStartup.addListener(() => {
  initNotificationSystem();
});

// Make sure polling survives service worker inactivity
chrome.runtime.onConnect.addListener(() => {});
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'wake') sendResponse({ok:true});
});

// ─── Init ────────────────────────────────────────────────────
async function initNotificationSystem() {
  try {
    const res = await fetch(`${API_URL}/api/info`);
    const info = await res.json();
    dbusAvailable = info.dbus_available;
  } catch {
    dbusAvailable = false;
  }

  if (dbusAvailable) {
    // Server handles D-Bus, skip extension notifications
    startHeartbeat();
    return;
  }

  // No D-Bus — extension handles notifications via custom popup
  startHeartbeat();
  startPolling();
}

// ─── Heartbeat ───────────────────────────────────────────────
function startHeartbeat() {
  if (heartbeatInterval) clearInterval(heartbeatInterval);
  heartbeatInterval = setInterval(() => {
    fetch(`${API_URL}/api/extension/heartbeat`, { method: 'POST' }).catch(() => {});
  }, 30000);
  // Send initial heartbeat
  fetch(`${API_URL}/api/extension/heartbeat`, { method: 'POST' }).catch(() => {});
}

// ─── Notification Polling ────────────────────────────────────
function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollQueue, 5000);
  pollQueue();
}

function pollQueue() {
  fetch(`${API_URL}/api/queue`)
    .then(r => r.json())
    .then(jobs => processJobs(jobs))
    .catch(() => {});
}

function processJobs(jobs) {
  const seen = {};
  for (const job of jobs) {
    const prev = prevJobs[job.id];
    seen[job.id] = true;

    if (!prev) {
      // New job appeared
      prevJobs[job.id] = job.status;
      continue;
    }

    if (prev === 'downloading' && job.status === 'completed') {
      showNotification('completed', job);
    } else if (prev === 'downloading' && job.status === 'failed') {
      showNotification('failed', job);
    } else if (!prev && job.status === 'completed') {
      // Was already complete when we started tracking
      showNotification('completed', job);
    }

    prevJobs[job.id] = job.status;
  }

  // Clean up stale entries
  for (const id in prevJobs) {
    if (!seen[id]) delete prevJobs[id];
  }
}

// ─── Show Notification Window ────────────────────────────────
function showNotification(type, job) {
  const thumb = job.video_id
    ? `https://i.ytimg.com/vi/${job.video_id}/mqdefault.jpg`
    : '';

  let meta = job.quality || '';
  if (type === 'completed') {
    const size = job.file_size ? formatBytes(job.file_size) : '';
    meta += ` • mp4` + (size ? ` • ${size}` : '');
  } else if (type === 'failed') {
    meta = job.error_message || 'Unknown error';
  } else {
    meta += ' • Added to Queue';
  }

  const params = new URLSearchParams({
    type,
    title: type === 'completed' ? 'Download Complete' : type === 'failed' ? 'Download Failed' : 'Download Started',
    video: job.title || job.video_id || 'Unknown',
    meta,
    thumb,
    jobId: job.id
  });
  const url = chrome.runtime.getURL('notification.html') + '?' + params.toString();

  chrome.windows.getLastFocused({}, (win) => {
    chrome.windows.create({
      url,
      type: 'popup',
      width: 420,
      height: 130,
      left: win.left + win.width - 444,
      top: win.top + win.height - 160
    });
  });
}

function formatBytes(b) {
  if (!b || b === 0) return '0 B';
  for (const u of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (b < 1024) return b.toFixed(1) + ' ' + u;
    b /= 1024;
  }
  return b.toFixed(1) + ' PB';
}

// ─── Context Menu Handler ────────────────────────────────────
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== 'yt-dl-download') return;
  const url = info.linkUrl;
  if (!url) return;

  const result = await chrome.storage.local.get(['defaultQuality']);
  const quality = result.defaultQuality || DEFAULT_QUALITY;

  try {
    const res = await fetch(`${API_URL}/api/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, quality })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Failed to queue download');
    }
  } catch (err) {
    // Only show chrome notification if D-Bus isn't available
    if (!dbusAvailable) {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icons/icon48.png',
        title: 'yt-dl Error',
        message: err.message || 'Daemon not running'
      });
    }
  }
});
