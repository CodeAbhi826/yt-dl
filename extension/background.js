// yt-dl Extension Background Script
const API_URL = 'http://127.0.0.1:5000';
const DEFAULT_QUALITY = '720p';
let prevJobs = {};
let sourceTabs = {};

// ─── Auth helper ──────────────────────────────────────────────
async function getAuthHeaders() {
  const { apiKey } = await chrome.storage.local.get('apiKey');
  return apiKey ? { 'Authorization': 'Bearer ' + apiKey } : {};
}

async function apiFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}), ...(await getAuthHeaders()) };
  return fetch(`${API_URL}${path}`, { ...opts, headers });
}

// ─── Persist prevJobs ─────────────────────────────────────────
async function loadPrevJobs() {
  const { prevJobs: stored } = await chrome.storage.local.get('prevJobs');
  prevJobs = stored || {};
}

async function savePrevJobs() {
  await chrome.storage.local.set({ prevJobs });
}

// ─── Context Menu ───────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'yt-dl-download',
    title: 'Download with yt-dl',
    contexts: ['link', 'video', 'audio']
  });
  chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 });
  chrome.alarms.create('poll', { periodInMinutes: 0.5 });
  loadPrevJobs();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 });
  chrome.alarms.create('poll', { periodInMinutes: 0.5 });
  loadPrevJobs();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'heartbeat') {
    apiFetch('/api/extension/heartbeat', { method: 'POST' }).catch(() => {});
  } else if (alarm.name === 'poll') {
    pollQueue();
  }
});

// ─── Notification Polling ────────────────────────────────────
function pollQueue() {
  apiFetch('/api/queue')
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
      if (prevJobs.__initialized) {
        if (job.status === 'downloading') {
          showNotification('started', job);
        } else if (job.status === 'completed') {
          showNotification('completed', job);
        }
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
    if (!seen[id] && id !== '__initialized') delete prevJobs[id];
  }
  prevJobs.__initialized = true;
  savePrevJobs();
}

// ─── Show In-Page Toast ──────────────────────────────────────
function showNotification(type, job) {
  const thumb = job.video_id
    ? `https://i.ytimg.com/vi/${job.video_id}/mqdefault.jpg`
    : '';

  const titles = {
    completed: 'Download Complete',
    failed: 'Download Failed',
    started: 'Download Started'
  };

  let meta = job.quality || '';
  if (type === 'completed') {
    meta += ' • mp4';
    if (job.file_size) meta += ' • ' + formatBytes(job.file_size);
  } else if (type === 'failed') {
    meta = job.error_message || 'Unknown error';
  } else {
    meta += ' • Downloading';
  }

  const data = {
    type,
    title: titles[type] || 'yt-dl',
    videoTitle: job.title || job.video_id || 'Unknown',
    thumb,
    jobId: job.id,
    meta
  };

  // Prefer source tab if known
  const targetTabId = sourceTabs[job.id];
  if (targetTabId) {
    chrome.scripting.executeScript({
      target: { tabId: targetTabId },
      func: injectToast,
      args: [data]
    }).catch(() => {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs.length === 0) {
          fallbackNative(type, job, titles[type] || 'yt-dl');
        } else {
          chrome.scripting.executeScript({
            target: { tabId: tabs[0].id },
            func: injectToast,
            args: [data]
          }).catch(() => fallbackNative(type, job, titles[type] || 'yt-dl'));
        }
      });
    });
    delete sourceTabs[job.id];
    return;
  }

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs.length === 0) {
      fallbackNative(type, job, titles[type] || 'yt-dl');
      return;
    }
    let succeeded = 0;
    let total = tabs.length;
    for (const tab of tabs) {
      if (tab.url && (tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://') || tab.url.startsWith('about:'))) {
        total--;
        continue;
      }
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: injectToast,
        args: [data]
      }).then(() => { succeeded++; })
        .catch(() => {})
        .finally(() => {
          if (total > 0 && --total === 0 && succeeded === 0) {
            fallbackNative(type, job, titles[type] || 'yt-dl');
          }
        });
    }
    if (total === 0) {
      fallbackNative(type, job, titles[type] || 'yt-dl');
    }
  });
}

// ─── Self-contained toast function (runs inside each page) ───
function injectToast(data) {
  try {
    var styleId = 'ytdl-toast-style';
    var contId = 'ytdl-toast-container';
    var doc = document;
    var head = doc.head || doc.querySelector('head');
    var body = doc.body;
    if (!head || !body) return;

    if (!doc.getElementById(styleId)) {
      var s = doc.createElement('style');
      s.id = styleId;
      s.textContent =
        '#ytdl-toast-container{position:fixed;bottom:20px;right:20px;z-index:2147483647;display:flex;flex-direction:column;gap:8px;pointer-events:none}' +
        '.ytdl-toast{pointer-events:auto;width:400px;background:#1b1b1b;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.06);display:flex;overflow:hidden;animation:ytdl-slide-in .3s cubic-bezier(0.16,1,0.3,1) forwards;font-family:Inter,-apple-system,BlinkMacSystemFont,Segoe UI,system-ui,sans-serif}' +
        '.ytdl-toast.ytdl-exit{animation:ytdl-slide-out .25s cubic-bezier(0.55,0,1,0.45) forwards}' +
        '@keyframes ytdl-slide-in{from{opacity:0;transform:translateX(60px) scale(.96)}to{opacity:1;transform:translateX(0) scale(1)}}' +
        '@keyframes ytdl-slide-out{from{opacity:1;transform:translateX(0) scale(1)}to{opacity:0;transform:translateX(60px) scale(.96)}}' +
        '.ytdl-toast-accent{width:4px;flex-shrink:0;border-radius:4px 0 0 4px}' +
        '.ytdl-toast--started .ytdl-toast-accent{background:#3ea6ff}' +
        '.ytdl-toast--completed .ytdl-toast-accent{background:#2ecc71}' +
        '.ytdl-toast--queued .ytdl-toast-accent{background:#888}' +
        '.ytdl-toast--failed .ytdl-toast-accent{background:#f39c12}' +
        '.ytdl-toast-thumb{width:120px;height:68px;flex-shrink:0;margin:10px;border-radius:8px;overflow:hidden;align-self:center}' +
        '.ytdl-toast-thumb img{width:100%;height:100%;object-fit:cover;display:block}' +
        '.ytdl-toast-body{flex:1;min-width:0;padding:10px 10px 10px 0;display:flex;flex-direction:column;justify-content:center;gap:1px}' +
        '.ytdl-toast-app{font-size:12px;font-weight:600;color:#888;letter-spacing:.02em;line-height:1.3}' +
        '.ytdl-toast-title{font-size:13px;font-weight:600;color:#fff;line-height:1.3}' +
        '.ytdl-toast-video{font-size:13px;font-weight:400;color:#bbb;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
        '.ytdl-toast-meta{font-size:11px;color:#777}' +
        '.ytdl-toast-row{display:flex;align-items:center;gap:6px;margin-top:3px}' +
        '.ytdl-toast-badge{font-size:10px;display:inline-flex;align-items:center;justify-content:center;border-radius:50%;width:14px;height:14px;font-weight:700;vertical-align:middle;margin-left:4px;background:#2ecc71;color:#1b1b1b}' +
        '.ytdl-toast-retry{font-size:12px;font-weight:600;color:#3ea6ff;cursor:pointer;background:none;border:none;padding:0;margin-left:auto;line-height:1;font-family:inherit}' +
        '.ytdl-toast-retry:hover{color:#6abfff}';
      head.appendChild(s);
    }

    var container = doc.getElementById(contId);
    if (!container) {
      container = doc.createElement('div');
      container.id = contId;
      body.appendChild(container);
    }

    var toast = doc.createElement('div');
    toast.className = 'ytdl-toast ytdl-toast--' + data.type;

    var accent = doc.createElement('div');
    accent.className = 'ytdl-toast-accent';
    toast.appendChild(accent);

    if (data.thumb) {
      var wrap = doc.createElement('div');
      wrap.className = 'ytdl-toast-thumb';
      var img = doc.createElement('img');
      img.src = data.thumb;
      img.alt = '';
      wrap.appendChild(img);
      toast.appendChild(wrap);
    }

    var bodyEl = doc.createElement('div');
    bodyEl.className = 'ytdl-toast-body';

    var app = doc.createElement('div');
    app.className = 'ytdl-toast-app';
    app.textContent = 'yt-dl';
    if (data.type === 'completed') {
      app.innerHTML = 'yt-dl <span class="ytdl-toast-badge">&#10003;</span>';
    }
    bodyEl.appendChild(app);

    var ttl = doc.createElement('div');
    ttl.className = 'ytdl-toast-title';
    ttl.textContent = data.title;
    bodyEl.appendChild(ttl);

    if (data.videoTitle) {
      var vt = doc.createElement('div');
      vt.className = 'ytdl-toast-video';
      vt.textContent = data.videoTitle;
      bodyEl.appendChild(vt);
    }

    var row = doc.createElement('div');
    row.className = 'ytdl-toast-row';

    var metaEl = doc.createElement('span');
    metaEl.className = 'ytdl-toast-meta';
    metaEl.textContent = data.meta;
    row.appendChild(metaEl);

    if (data.type === 'failed' && data.jobId) {
      var retryBtn = doc.createElement('button');
      retryBtn.className = 'ytdl-toast-retry';
      retryBtn.textContent = 'Retry';
      retryBtn.addEventListener('click', function () {
        fetch('http://127.0.0.1:5000/api/jobs/' + data.jobId + '/retry', { method: 'POST' }).catch(function () {});
        removeToast(toast);
      });
      row.appendChild(retryBtn);
    }

    bodyEl.appendChild(row);
    toast.appendChild(bodyEl);
    container.appendChild(toast);

    var timeout = data.type === 'failed' ? 5000 : 3000;
    setTimeout(function () {
      if (toast.classList.contains('ytdl-exit')) return;
      toast.classList.add('ytdl-exit');
      toast.addEventListener('animationend', function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      });
    }, timeout);

    function removeToast(el) {
      if (el.classList.contains('ytdl-exit')) return;
      el.classList.add('ytdl-exit');
      el.addEventListener('animationend', function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      });
    }
  } catch (e) {
    // Silently fail - native fallback will handle
  }
}

async function fallbackNative(type, job, title) {
  const { nativeNotifications } = await chrome.storage.local.get(['nativeNotifications']);
  if (nativeNotifications === false) return;
  let message = (job.title || job.video_id || 'Unknown');
  if (type === 'completed') {
    const size = job.file_size ? formatBytes(job.file_size) : '';
    message += '\n' + (job.quality || '') + ' • mp4' + (size ? ' • ' + size : '');
  } else if (type === 'failed') {
    message += '\n' + (job.error_message || 'Unknown error');
  } else {
    message += '\n' + (job.quality || '') + ' • Downloading';
  }
  chrome.notifications.create(job.id, {
    type: 'basic',
    iconUrl: 'icons/icon48.png',
    title,
    message,
    priority: type === 'failed' ? 2 : 1,
    buttons: type === 'failed' ? [{ title: 'Retry' }] : []
  });
}

chrome.notifications.onClicked.addListener((id) => {
  chrome.tabs.create({ url: `${API_URL}/` });
});

chrome.notifications.onButtonClicked.addListener((id, buttonIndex) => {
  if (buttonIndex === 0) {
    apiFetch(`/api/jobs/${id}/retry`, { method: 'POST' }).catch(() => {});
  }
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
  const url = info.linkUrl || info.srcUrl || info.pageUrl;
  if (!url) return;

  const result = await chrome.storage.local.get(['defaultQuality']);
  const quality = result.defaultQuality || DEFAULT_QUALITY;

  try {
    const res = await apiFetch('/api/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, quality })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Failed to queue download');
    }

    const body = await res.json();

    if (body.job_id) {
      sourceTabs[body.job_id] = tab.id;
    }

    const titles = { queued: 'Added to Queue' };
    const thumb = '';
    const meta = quality ? quality + ' • Added to Queue' : 'Added to Queue';

    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: injectToast,
      args: [{
        type: 'queued',
        title: titles.queued,
        videoTitle: body.title || '',
        thumb,
        jobId: body.job_id || '',
        meta
      }]
    }).catch(() => {});
  } catch (err) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs.length === 0) {
        chrome.notifications.create({
          type: 'basic',
          iconUrl: 'icons/icon48.png',
          title: 'yt-dl Error',
          message: err.message || 'Daemon not running'
        });
        return;
      }
      chrome.scripting.executeScript({
        target: { tabId: tabs[0].id },
        func: injectToast,
        args: [{
          type: 'failed',
          title: 'yt-dl Error',
          videoTitle: err.message || 'Daemon not running',
          thumb: '',
          jobId: '',
          meta: 'Check that the daemon is running on localhost:5000'
        }]
      }).catch(() => {
        chrome.notifications.create({
          type: 'basic',
          iconUrl: 'icons/icon48.png',
          title: 'yt-dl Error',
          message: err.message || 'Daemon not running'
        });
      });
    });
  }
});
