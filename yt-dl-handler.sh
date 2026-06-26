#!/bin/bash
# yt-dl headless handler
# Called when you right-click a YouTube link → Open With → yt-dl

URL="$1"

if [[ "$URL" != *"youtube.com"* ]] && [[ "$URL" != *"youtu.be"* ]]; then
    notify-send --app-name=yt-dl --urgency=critical "yt-dl" "Not a YouTube URL:
$URL"
    exit 1
fi

if ! curl -sf http://localhost:5000/health > /dev/null 2>&1; then
    systemctl --user start yt-dl
    sleep 2
fi

QUALITY=$(python3 -c "import json, os; cfg = json.load(open(os.path.expanduser('~/.local/share/yt-dl/config.json'))); print(cfg.get('default_quality','720p'))")

curl -s -X POST http://localhost:5000/api/add \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json, sys; d={'url': sys.argv[1], 'quality': sys.argv[2]}; print(json.dumps(d))" "$URL" "$QUALITY")" \
    > /dev/null

notify-send --app-name=yt-dl "yt-dl" "Download queued
$URL"
