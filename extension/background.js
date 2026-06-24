// yt-dl Extension Background Script
const API_URL = 'http://127.0.0.1:5000';
const DEFAULT_QUALITY = '720p';
let prevJobs = {};

// ─── Context Menu ───────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'yt-dl-download',
    title: 'Download with yt-dl',
    contexts: ['link']
  });
  chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 });
  chrome.alarms.create('poll', { periodInMinutes: 0.1 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 });
  chrome.alarms.create('poll', { periodInMinutes: 0.1 });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'heartbeat') {
    fetch(`${API_URL}/api/extension/heartbeat`, { method: 'POST' }).catch(() => {});
  } else if (alarm.name === 'poll') {
    pollQueue();
  }
});

// ─── Notification Polling ────────────────────────────────────
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
      if (job.status === 'downloading') {
        showNotification('started', job);
      } else if (job.status === 'completed') {
        showNotification('completed', job);
      }
      prevJobs[job.id] = job.status;
      continue;
    }

    if (prev === 'downloading' && job.status === 'completed') {
      showNotification('completed', job);
    } else if (prev === 'downloading' && job.status === 'failed') {
      showNotification('failed', job);
    } else if (prev === 'queued' && job.status === 'downloading') {
      showNotification('started', job);
    }

    prevJobs[job.id] = job.status;
  }

  for (const id in prevJobs) {
    if (!seen[id]) delete prevJobs[id];
  }
}

// ─── Show Notification Toast ────────────────────────────────
function showNotification(type, job) {
  const titles = { completed: 'Download Complete', failed: 'Download Failed', started: 'Download Started' };
  const title = titles[type] || 'yt-dl';

  let message = (job.title || job.video_id || 'Unknown') + '\n';
  if (type === 'completed') {
    const size = job.file_size ? formatBytes(job.file_size) : '';
    message += (job.quality || '') + ' • mp4' + (size ? ` • ${size}` : '');
  } else if (type === 'failed') {
    message += job.error_message || 'Unknown error';
  } else {
    message += (job.quality || '') + ' • Downloading';
  }

  const notifOptions = {
    type: 'basic',
    iconUrl: 'icons/icon48.png',
    title,
    message,
    priority: type === 'failed' ? 2 : 1,
  };

  if (job.video_id) {
    notifOptions.type = 'image';
    notifOptions.imageUrl = `https://i.ytimg.com/vi/${job.video_id}/mqdefault.jpg`;
  }

  if (type === 'failed') {
    notifOptions.buttons = [{ title: 'Retry' }];
  }

  chrome.notifications.create(job.id, notifOptions);
}

chrome.notifications.onButtonClicked.addListener((id, buttonIndex) => {
  if (buttonIndex === 0) {
    fetch(`${API_URL}/api/jobs/${id}/retry`, { method: 'POST' }).catch(() => {});
  }
});

chrome.notifications.onClicked.addListener((id) => {
  chrome.tabs.create({ url: `${API_URL}/` });
});

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
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl Error',
      message: err.message || 'Daemon not running'
    });
  }
});
