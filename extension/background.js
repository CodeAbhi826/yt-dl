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
  } else if (type === 'started') {
    meta += ' • Downloading';
  } else {
    meta += ' • Added to Queue';
  }

  const params = new URLSearchParams({
    type,
    title: type === 'completed' ? 'Download Complete' : type === 'failed' ? 'Download Failed' : type === 'started' ? 'Download Started' : 'Download Queued',
    video: job.title || job.video_id || 'Unknown',
    meta,
    thumb,
    jobId: job.id
  });
  const url = chrome.runtime.getURL('notification.html') + '?' + params.toString();

  chrome.windows.getLastFocused({}, (win) => {
    const left = win ? win.left + win.width - 444 : screen.availWidth - 444;
    const top = win ? win.top + win.height - 160 : screen.availHeight - 160;
    chrome.windows.create({
      url,
      type: 'popup',
      width: 420,
      height: 130,
      left: Math.max(0, left),
      top: Math.max(0, top)
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
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl Error',
      message: err.message || 'Daemon not running'
    });
  }
});
