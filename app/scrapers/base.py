"""Interfaccia comune per gli scraper dei portali immobiliari.

La modularità sta qui: ogni portale implementa PortalScraper. Se un portale
cambia l'HTML, si tocca SOLO il suo adapter, mai il resto della piattaforma.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Annuncio:
    annuncio_id: str                 # ID univoco estratto dall'URL
    url: str
    prezzo: Optional[float] = None
    tipologia: str = ""
    superficie_mq: str = ""
    locali: str = ""
    bagni: str = ""
    piano: str = ""
    indirizzo: str = ""
    contratto: str = ""              # vendita | affitto
    privato_agenzia: str = ""        # privato | agenzia
    nome_agenzia: str = ""
    riferimento: str = ""
    data_pubblicazione: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PortalScraper(ABC):
    """Interfaccia comune: ogni portale implementa scrape()."""

    @property
    @abstractmethod
    def portal_id(self) -> str:
        """Identificativo univoco, es. 'idealista'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Nome leggibile, es. 'Idealista'."""

    @abstractmethod
    def extract_id(self, url: str) -> str:
        """Estrae l'ID univoco dell'annuncio dall'URL."""

    @abstractmethod
    def scrape(self, url_ricerca: str, filtri: dict | None = None) -> list[Annuncio]:
        """Scarica la pagina di ricerca e restituisce la lista di annunci.

        url_ricerca: URL della ricerca già filtrata sul portale (metodo primario).
        filtri: filtri strutturati opzionali (comune, tipologia, prezzi, mq).
        """
