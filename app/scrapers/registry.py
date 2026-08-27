"""Registry degli scraper: qui si registra un portale nuovo senza toccare il resto."""
from .base import PortalScraper
from .idealista import IdealistaScraper
from .immobiliare import ImmobiliareScraper

_REGISTRY: dict[str, PortalScraper] = {}


def register(scraper: PortalScraper):
    _REGISTRY[scraper.portal_id] = scraper


def get_scraper(portal_id: str) -> PortalScraper:
    if portal_id not in _REGISTRY:
        raise KeyError(f"Portale sconosciuto: {portal_id}. Disponibili: {list(_REGISTRY)}")
    return _REGISTRY[portal_id]


def list_portals() -> list[dict]:
    return [{"id": p.portal_id, "nome": p.display_name} for p in _REGISTRY.values()]


def init_registry():
    register(IdealistaScraper())
    register(ImmobiliareScraper())
