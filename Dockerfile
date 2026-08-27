FROM python:3.12-slim

WORKDIR /app

# gosom binary (Google Maps scraper)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates golang-go \
    && curl -fsSL -o /usr/local/bin/gm_scraper \
       https://github.com/gosom/google-maps-scraper/releases/download/v1.17.4/google_maps_scraper-1.17.4-linux-amd64 \
    && chmod +x /usr/local/bin/gm_scraper

# Browser Playwright per gosom (playwright-go v0.6100.0 -> chromium-1228)
# installati con la STESSA versione del go.mod di gosom, poi via Go
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN GOFLAGS=-mod=mod go install github.com/mxschmitt/playwright-go/cmd/playwright@v0.6100.0 \
    && /root/go/bin/playwright install chromium \
    && apt-get purge -y golang-go && apt-get autoremove -y && rm -rf /root/go/pkg /root/.cache/go-build /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /data/downloads

ENV SCRAP_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
