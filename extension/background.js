// yt-dl Extension Background Script
const API_URL = 'http://127.0.0.1:5000';
const DEFAULT_QUALITY = '720p';

// Create context menu
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
});

// Handle context menu click
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

    const data = await res.json();

    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl',
      message: 'Download queued: ' + (data.job_id || 'unknown')
    });
  } catch (err) {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl Error',
      message: err.message || 'Daemon not running. Start with: systemctl --user start yt-dl'
    });
  }
});

// Handle extension icon click
chrome.action.onClicked.addListener(async (tab) => {
  const url = tab.url;
  if (!url.includes('youtube.com/watch') && !url.includes('youtu.be/')) {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl',
      message: 'Not a YouTube video page'
    });
    return;
  }

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

    const data = await res.json();
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl',
      message: 'Download queued: ' + (data.job_id || 'unknown')
    });
  } catch (err) {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title: 'yt-dl Error',
      message: err.message || 'Daemon not running'
    });
  }
});
