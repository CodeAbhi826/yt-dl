FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask yt-dlp dbus-python

WORKDIR /app

COPY src/ ./src/

EXPOSE 5000

ENV PYTHONPATH=/app/src

CMD ["python3", "-u", "src/app.py"]
