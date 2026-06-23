# Publishing yt-dl Extension

## Chrome Web Store

1. Go to https://chrome.google.com/webstore/devconsole
2. Pay the one-time $5 registration fee
3. Click "New item" and upload `extension/` as a zip

### Required Assets

**Store Icon:** `extension/icons/icon128.png` (128x128, already exists)

**Screenshots (1280x800 or 640x400):**
1. Popup quality selector — `extension/store/screenshot-popup.png`
2. Dashboard overview — screenshot of http://localhost:5000
3. Settings page — screenshot of /settings
4. Toast notification — screenshot of notification popup

**Promo Tiles (optional):**
- Small Promo Tile: 440x280px
- Large Promo Tile: 920x680px
- Marquee: 1400x560px

### Store Listing

**Name:** yt-dl
**Short description:** One-click YouTube downloader — right-click, select quality, done.
**Full description:**

```text
yt-dl is a self-hosted YouTube download companion that integrates directly into your browser.

Features:
• Right-click any YouTube link → "Download with yt-dl"
• Quality selector popup (144p to 2160p + audio)
• Real-time dashboard with progress bars, speed, ETA
• Desktop notifications when downloads complete
• Playlist support with duplicate detection
• Configurable output naming for media servers

Requirements:
• yt-dl daemon running on localhost:5000
• Docker: docker compose up
   OR
• Manual: pip install yt-dlp flask dbus-python

This extension requires the yt-dl backend server to function.
```

**Category:** Productivity
**Language:** English
**Homepage URL:** https://github.com/CodeAbhi826/yt-dl
**Privacy Policy:** https://github.com/CodeAbhi826/yt-dl (no user data collected)

### Permissions Justification

| Permission | Reason |
|------------|--------|
| `storage` | Save default quality preference |
| `contextMenus` | Add "Download with yt-dl" to right-click menu |
| `activeTab` | Get current YouTube tab URL |
| `notifications` | Show download completion toasts |
| `http://127.0.0.1:5000/*` | Communicate with local yt-dl daemon |
| `*://*.youtube.com/*` | Access YouTube video pages |
| `*://youtu.be/*` | Access shortened YouTube URLs |

---

## Firefox Add-ons

1. Navigate to https://addons.mozilla.org/en-US/developers/
2. Sign in and click "Submit a New Add-on"
3. Upload `extension/` as a zip

### Required Changes for Firefox

Firefox uses `manifest.json` v3 but has some differences:

- `background.service_worker` → `background.scripts` in Firefox MV3
- No `chrome.sidePanel` API
- Test thoroughly before submission

### Firefox-Specific manifest.json adjustments:

```json
{
  "browser_specific_settings": {
    "gecko": {
      "id": "yt-dl@codeabhi826.github.io",
      "strict_min_version": "109.0"
    }
  },
  "background": {
    "scripts": ["background.js"]
  }
}
```
