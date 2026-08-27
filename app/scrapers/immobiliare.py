"""Adapter Immobiliare.it — parsing con requests + BeautifulSoup.

Stessa interfaccia di Idealista: se Immobiliare.it cambia l'HTML, si sistema
SOLO questo file.
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..anti_bot import fetch_html, polite_delay
from .. import config
from .base import Annuncio, PortalScraper

_BASE = "https://www.immobiliare.it"


def _listing_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    query = parsed.query
    if "pag=" in query:
        query = re.sub(r"pag=\d+", f"pag={page}", query)
    elif page > 1:
        query = f"{query}&pag={page}" if query else f"pag={page}"
    return parsed._replace(query=query).geturl()


class ImmobiliareScraper(PortalScraper):
    @property
    def portal_id(self) -> str:
        return "immobiliare"

    @property
    def display_name(self) -> str:
        return "Immobiliare.it"

    def extract_id(self, url: str) -> str:
        m = re.search(r"/annunci/(\d+)/", url) or re.search(r"/(\d{6,})(?:/|$)", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            return m.group(1)
        return url

    def scrape(self, url_ricerca: str, filtri: dict | None = None) -> list[Annuncio]:
        filtri = filtri or {}
        annunci: list[Annuncio] = []
        max_pages = config.MAX_PAGES

        for page in range(1, max_pages + 1):
            page_url = _listing_page_url(url_ricerca, page)
            try:
                html = fetch_html(page_url)
            except RuntimeError as e:
                if page == 1:
                    raise
                break

            soup = BeautifulSoup(html, "lxml")
            cards = soup.select("li.nd-list__item, .in-realEstateResults li, [class*='nd-list__item']")
            if not cards:
                cards = soup.select('a[href*="/annunci/"], a[href*="annuncio"]')
                if not cards:
                    break

            found_here = 0
            for card in cards:
                link_el = card.select_one("a[href]") if card.name == "li" else card
                if link_el is None:
                    continue
                href = link_el.get("href") or ""
                if "/annunci/" not in href and "annuncio" not in href and re.search(r"/\d{6,}/", href) is None:
                    continue
                full_url = urljoin(_BASE + "/", href.lstrip("/"))
                ann_id = self.extract_id(full_url)
                annunci.append(Annuncio(
                    annuncio_id=ann_id,
                    url=full_url,
                    prezzo=self._parse_price(card),
                    tipologia=self._text(card, ".it-card__title, .in-card__title, h2, h3"),
                    superficie_mq=self._text(card, ".it-card__features, .in-feat__data, [aria-label='superficie']"),
                    locali=self._text(card, "[aria-label='locali'], .it-card__features"),
                    bagni=self._text(card, "[aria-label='bagni']"),
                    piano=self._text(card, "[aria-label='piano']"),
                    indirizzo=self._text(card, ".it-card__location, .in-location, .it-card__address"),
                    contratto="vendita" if "vendita" in page_url or "vendo" in page_url.lower() else "affitto",
                    privato_agenzia="agenzia" if card.select_one(".it-card__agency, .in-referent, [class*='agency']") else "privato",
                    nome_agenzia=self._text(card, ".it-card__agency, .in-referent"),
                    riferimento=ann_id,
                ))
                found_here += 1
                if len(annunci) >= config.MAX_ANNUNCI:
                    return annunci
            if found_here == 0:
                break
            polite_delay()

        return annunci

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return el.get_text(" ", strip=True) if el else ""

    @staticmethod
    def _parse_price(card) -> float | None:
        el = card.select_one(".it-card__amount, .in-detail__mainFeaturesPrice, [class*='price']")
        if el is None:
            return None
        raw = el.get_text(strip=True)
        digits = re.sub(r"[^\d]", "", raw.split("/")[0])
        if not digits:
            return None
        try:
            return float(digits)
        except ValueError:
            return None
