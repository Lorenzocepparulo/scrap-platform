"""Adapter Immobiliare.it — parsing con requests + BeautifulSoup.

Stessa interfaccia di Idealista: se Immobiliare.it cambia l'HTML, si sistema
SOLO questo file.

⚠️ Struttura reale (verificata 2026-08-27 su HTML live via ZenRows):
le card usano classi CSS hashate (Title_title__*, Price_price__*,
FeatureList_item__*, AgencyLogo_*) e i dati sono in attributi aria-label.
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..anti_bot import fetch_html, polite_delay
from .. import config
from .base import Annuncio, PortalScraper

_BASE = "https://www.immobiliare.it"

# Selettori reali della pagina di ricerca (classi hashate Next.js)
SEL_CARD = 'li[class*="ListItem_item__card"], li.nd-list__item'
SEL_TITLE = 'a[class*="Title_title"], a[href*="/annunci/"]'
SEL_PRICE = '[class*="Price_price"]'
SEL_LOGO = 'img[class*="AgencyLogo"]'


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
            cards = self._find_cards(soup)
            if not cards:
                break

            found_here = 0
            for card in cards:
                ann = self._parse_card(card, page_url)
                if ann is None:
                    continue
                annunci.append(ann)
                found_here += 1
                if len(annunci) >= config.MAX_ANNUNCI:
                    return annunci
            if found_here == 0:
                break
            polite_delay()

        return annunci

    @staticmethod
    def _find_cards(soup):
        """Card annunci reali. Filtra breadcrumb e voci di menu che matchano nd-list__item."""
        cards = soup.select('li[class*="ListItem_item__card"]')
        if cards:
            return cards
        # fallback: qualsiasi li con link a /annunci/
        out = []
        for li in soup.select("li.nd-list__item"):
            a = li.select_one('a[href*="/annunci/"]')
            if a:
                out.append(li)
        return out

    def _parse_card(self, card, page_url: str) -> Annuncio | None:
        link_el = card.select_one(SEL_TITLE)
        if link_el is None:
            return None
        href = link_el.get("href") or ""
        if "/annunci/" not in href and re.search(r"/\d{6,}/", href) is None:
            return None
        full_url = urljoin(_BASE + "/", href.lstrip("/"))
        ann_id = self.extract_id(full_url)
        if not ann_id:
            return None

        # id diretto dal contenitore (attributo id del li) se disponibile
        cid = card.get("id") or ann_id

        titolo = self._text(link_el)
        features = self._features(card)

        logo = card.select_one(SEL_LOGO)
        agenzia = logo.get("alt", "").strip() if logo else ""
        privato_agenzia = "agenzia" if agenzia else "privato"

        return Annuncio(
            annuncio_id=cid,
            url=full_url,
            prezzo=self._parse_price(card),
            tipologia=titolo,
            superficie_mq=features.get("mq", ""),
            locali=features.get("locali", ""),
            bagni=features.get("bagni", ""),
            piano=features.get("piano", ""),
            indirizzo=self._parse_indirizzo(titolo),
            contratto="vendita" if "vendita" in page_url or "vendo" in page_url.lower() else "affitto",
            privato_agenzia=privato_agenzia,
            nome_agenzia=agenzia,
            riferimento=ann_id,
            data_pubblicazione="",
        )

    @staticmethod
    def _features(card) -> dict:
        """Estrae i dati dagli aria-label della card (struttura reale)."""
        out = {"locali": "", "mq": "", "bagni": "", "piano": ""}
        for el in card.select("[aria-label]"):
            label = (el.get("aria-label") or "").strip()
            if not label or label in ("nascondi annuncio", "Salva tra i preferiti"):
                continue
            if re.search(r"\d+\s*-\s*\d+\s*locali|\d+\s*locali", label):
                out["locali"] = label
            elif "m²" in label or "mq" in label.lower():
                out["mq"] = label
            elif re.search(r"\d+\s*bagno", label):
                out["bagni"] = label
            elif label.lower().startswith("piano"):
                out["piano"] = label
        return out

    @staticmethod
    def _parse_indirizzo(titolo: str) -> str:
        """L'indirizzo è spesso nel titolo: 'Trilocale via Gaspare Spontini 9, Buenos Aires, Milano'."""
        m = re.search(
            r"(?:via|viale|piazza|corso|vicolo|largo|piazzale|borgo)\s+[^,]+,\s*[^,]+",
            titolo,
            re.IGNORECASE,
        )
        return m.group(0) if m else ""

    @staticmethod
    def _text(el) -> str:
        return el.get_text(" ", strip=True) if el else ""

    @staticmethod
    def _parse_price(card) -> float | None:
        el = card.select_one(SEL_PRICE)
        if el is None:
            return None
        raw = el.get_text(" ", strip=True)
        m = re.search(r"€\s*([\d.]+)", raw.replace(".", "").replace(",", "."))
        if not m:
            digits = re.sub(r"[^\d]", "", raw.split("/")[0])
            if not digits:
                return None
            try:
                return float(digits)
            except ValueError:
                return None
        try:
            return float(m.group(1))
        except ValueError:
            return None
