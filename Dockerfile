FROM python:3.12-slim

WORKDIR /app

# gosom binary (Google Maps scraper) + browser playwright richiesto da gosom
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/gm_scraper \
       https://github.com/gosom/google-maps-scraper/releases/download/v1.17.4/google_maps_scraper-1.17.4-linux-amd64 \
    && chmod +x /usr/local/bin/gm_scraper

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium --with-deps \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY app ./app
RUN mkdir -p /data/downloads

ENV SCRAP_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
