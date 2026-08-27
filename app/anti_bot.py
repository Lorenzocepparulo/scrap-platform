"""Anti-bot: user-agent rotanti, delay randomici, proxy opzionale.

Tier 1: richieste HTTP dirette (requests, UA rotanti).
Tier 2: se il portale risponde 403 (DataDome/Cloudflare), fallback su browser
headless (Playwright + Chromium) con la stessa interfaccia: fetch_html().
"""
import random
import time

import requests

from . import config


def random_user_agent() -> str:
    return random.choice(config.USER_AGENTS)


def polite_delay():
    """Delay randomico tra le richieste per non martellare i portali."""
    time.sleep(random.uniform(config.DELAY_MIN, config.DELAY_MAX))


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    if config.PROXY_URL:
        s.proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL}
    return s


def _fetch_with_browser(url: str, timeout: int = 60) -> str:
    """Fallback: browser Chromium headful (schermo virtuale via DISPLAY/Xvfb).

    DataDome/Cloudflare bloccano i browser headless e le richieste HTTP dirette,
    ma un browser headful vero risolve la challenge JS e lascia passare.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright non installato — impossibile il fallback browser")

    proxy = {"server": config.PROXY_URL} if config.PROXY_URL else None
    headless = not (os.environ.get("DISPLAY") or "")
    html = ""
    with sync_playwright() as p:
        browser = None
        try:
            # timeout sul launch: se Xvfb non risponde, fallisce subito invece di pendere
            browser = p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
                proxy=proxy,
                timeout=25000,
            )
        except Exception as e:
            if not headless:
                # display non disponibile: ripiega su headless
                try:
                    browser = p.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
                        proxy=proxy,
                        timeout=25000,
                    )
                except Exception as e2:
                    raise RuntimeError(f"Impossibile avviare Chromium: {e2}")
            else:
                raise RuntimeError(f"Impossibile avviare Chromium: {e}")
        ctx = browser.new_context(
            user_agent=random_user_agent(),
            locale="it-IT",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        try:
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            # aspetta che l'eventuale challenge JS (DataDome) si risolva e la pagina ricarichi
            for _ in range(12):
                page.wait_for_timeout(2500)
                html = page.content()
                if "item-link" in html or "inmueble" in html or "nd-list__item" in html or "it-card" in html:
                    return html
                if "cmsg" not in html and "datadome" not in html.lower():
                    return html
            html = page.content()
        finally:
            try:
                browser.close()
            except Exception:
                pass
    if not html or len(html) < 500:
        raise RuntimeError("Pagina vuota dal fallback browser")
    return html


def _fetch_via_antibot(url: str, timeout: int = 60) -> str:
    """Gateway anti-bot (ZenRows-style): supera DataDome/Cloudflare con un servizio esterno."""
    if not config.ANTIBOT_URL or not config.ANTIBOT_KEY:
        raise RuntimeError("Gateway anti-bot non configurato (SCRAP_ANTIBOT_URL/SCRAP_ANTIBOT_KEY)")
    from urllib.parse import urlencode
    target = f"{config.ANTIBOT_URL}?{urlencode({'url': url, 'apikey': config.ANTIBOT_KEY, 'js_render': 'true', 'premium_proxy': 'true'})}"
    resp = requests.get(target, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Gateway anti-bot: HTTP {resp.status_code}")
    return resp.text


def fetch_html(url: str, timeout: int = 30) -> str:
    """Fetch con UA rotante + retry; su 403 fa fallback al browser headless."""
    last_err = None
    # Se c'è un gateway anti-bot configurato, usalo direttamente (più affidabile)
    if config.ANTIBOT_URL and config.ANTIBOT_KEY:
        return _fetch_via_antibot(url, timeout=timeout)
    for attempt in range(2):
        s = make_session()
        try:
            resp = s.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code == 403:
                break  # passa subito al fallback browser
        except requests.RequestException as e:
            last_err = str(e)
        polite_delay()

    # Fallback browser (403/errore di rete)
    try:
        return _fetch_with_browser(url, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"Fetch fallito per {url}: {last_err} (browser: {e})")
