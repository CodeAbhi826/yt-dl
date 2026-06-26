FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask yt-dlp

RUN useradd -m -u 1000 ytdl

WORKDIR /app

COPY --chown=ytdl:ytdl src/ ./src/

USER ytdl

EXPOSE 5000

ENV PYTHONPATH=/app/src
ENV YTDL_BIND=0.0.0.0
ENV HOME=/home/ytdl

CMD ["python3", "-u", "src/app.py"]
