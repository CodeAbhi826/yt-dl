#!/bin/bash
# yt-dl headless handler
# Called when you right-click a YouTube link → Open With → yt-dl

URL="$1"

if [[ "$URL" != *"youtube.com"* ]] && [[ "$URL" != *"youtu.be"* ]]; then
    notify-send --app-name=yt-dl --urgency=critical "yt-dl" "Not a YouTube URL:
$URL"
    exit 1
fi

if ! curl -s http://localhost:5000/api/health > /dev/null 2>&1; then
    systemctl --user start yt-dl
    sleep 2
fi

QUALITY=$(python3 -c "import json; print(json.load(open('$HOME/.local/share/yt-dl/config.json')).get('default_quality','720p'))")
JSON_BODY=$(python3 -c "import json; d={'url':'$URL','quality':'$QUALITY'}; print(json.dumps(d))")

curl -s -X POST http://localhost:5000/api/add     -H "Content-Type: application/json"     -d "$JSON_BODY"     > /dev/null

notify-send --app-name=yt-dl "yt-dl" "Download queued
$URL"
