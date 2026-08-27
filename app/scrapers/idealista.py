"""Adapter Idealista — parsing riscritto da zero (requests + BeautifulSoup).

Il repo originale (martapanc/Idealista-Immobiliare-Scraper) dipende da
cloakbrowser (fragile). Qui manteniamo la stessa logica di estrazione ma con
richieste HTTP dirette, UA rotanti e delay randomici. Se Idealista cambia
l'HTML, si sistema SOLO questo file.
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..anti_bot import fetch_html, polite_delay
from .. import config
from .base import Annuncio, PortalScraper

_BASE = "https://www.idealista.it"


def _listing_page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    path = re.sub(r"/pagina-\d+\.htm$", "", path)
    if page <= 1:
        new_path = path + "/"
    else:
        new_path = f"{path}/pagina-{page}.htm"
    return parsed._replace(path=new_path).geturl()


class IdealistaScraper(PortalScraper):
    @property
    def portal_id(self) -> str:
        return "idealista"

    @property
    def display_name(self) -> str:
        return "Idealista"

    def extract_id(self, url: str) -> str:
        m = re.search(r"/inmueble/(\d+)\.htm", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{6,})\.htm", url)
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
            cards = soup.select("main#main-content article.item, article.item")
            if not cards:
                cards = soup.select('a.item-link[href*="inmueble"]')
                # se nemmeno i link ci sono, probabilmente blocco/DataDome o fine pagina
                if not cards:
                    break

            found_here = 0
            for card in cards:
                link_el = card.select_one('a.item-link[href*="inmueble"]') if card.name == "article" else card
                if link_el is None:
                    continue
                href = link_el.get("href") or ""
                if "/inmueble/" not in href:
                    continue
                full_url = urljoin(_BASE + "/", href.lstrip("/"))
                ann_id = self.extract_id(full_url)
                prezzo = self._parse_price(card)
                annunci.append(Annuncio(
                    annuncio_id=ann_id,
                    url=full_url,
                    prezzo=prezzo,
                    tipologia=self._text(card, ".item-title, .item-link h3, h3"),
                    superficie_mq=self._text(card, ".item-detail-char, .item-toolbar, .item-detail"),
                    locali=self._text(card, ".item-detail-char"),
                    indirizzo=self._text(card, ".item-address, .item-toolbar"),
                    contratto="vendita" if "compro" in page_url or "compravendita" in page_url else "affitto",
                    privato_agenzia="agenzia" if card.select_one(".item-toolbar") else "privato",
                    nome_agenzia=self._text(card, ".item-toolbar p, .item-toolbar__title"),
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
        el = card.select_one(".item-price, .item-price-amount, [data-testid='price']")
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
