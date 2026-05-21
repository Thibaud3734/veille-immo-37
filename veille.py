#!/usr/bin/env python3
"""
Veille immobilière commerciale — Indre-et-Loire (37)
Version cloud : email via SMTP Office 365
"""

import asyncio
import html as _html
import json
import logging
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────────────────
TODAY = datetime.now().strftime("%Y-%m-%d")
DEPT = "37"
DEPT_NAME = "Indre-et-Loire"

EMAIL_TO        = os.environ["EMAIL_TO"]          # t.segeon@la-ie.fr
SMTP_USER       = os.environ["SMTP_USER"]        # compte expéditeur Office 365
SMTP_PASS       = os.environ["SMTP_PASS"]        # mot de passe ou app password
EQUIMMOX_EMAIL  = os.environ.get("EQUIMMOX_EMAIL", "")
EQUIMMOX_PASS   = os.environ.get("EQUIMMOX_PASS", "")

# seen_listings stocké en JSON dans la variable d'env SEEN_JSON (GitHub Actions artifact)
SEEN_JSON_PATH = Path("seen_listings.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Villes du 37 ──────────────────────────────────────────────────────────────
CITIES_37 = [
    "tours-37000", "joue-les-tours-37300", "saint-pierre-des-corps-37700",
    "saint-cyr-sur-loire-37540", "chambray-les-tours-37170",
]

CITIES_37_VENTE = [
    "tours-37000", "joue-les-tours-37300", "saint-pierre-des-corps-37700",
    "saint-cyr-sur-loire-37540", "chambray-les-tours-37170",
    "ballan-mire-37510",
]

GEOLOCAUX_TYPES = [
    ("location", "bureau",           "Bureau — Location",           CITIES_37),
    ("location", "local-commercial", "Local commercial — Location", CITIES_37),
    ("location", "entrepot",         "Local d'activité — Location", CITIES_37),
    ("vente",    "bureau",           "Bureau — Vente",              CITIES_37_VENTE),
    ("vente",    "local-commercial", "Local commercial — Vente",    CITIES_37_VENTE),
    ("vente",    "entrepot",         "Local d'activité — Vente",    CITIES_37_VENTE),
]

ARTHUR_LOYD_SLUGS = ["bureau-location", "terrain-location"]

GEOLOCAUX_BASE      = "https://www.geolocaux.com"
ARTHUR_LOYD_BASE    = "https://www.arthur-loyd.com"
WEADVISOR_BASE      = "https://www.weadvisor.fr"
IMVALORIS_BASE      = "https://www.imvaloris.fr"
IMVALORIS_LIST      = "https://www.imvaloris.fr/consultez-nos-biens-immobiliers/"
IMVALORIS_KEYWORDS  = re.compile(r"local|bureau|commerce|activit|entrepôt|professionnel", re.I)
EQUIMMOX_LOGIN   = "https://app.equimmox.com/connexion"
EQUIMMOX_SEARCH  = (
    "https://app.equimmox.com/?pmn=&pmx=&cl=Office_Commercial&smn=&smx=&slt=1"
    "&dmn=&dmx=&oc=&act=true&kw=&dp=Indre-et-Loire&rg=&ct=&prmn=&prmx=&rd="
    "&p2n=&p2x=&ptp=&bin=&bex=&loc=&mef=true&xtc=&erp="
)

WEADVISOR_SEARCHES = [
    ("/bureaux-location/indre-et-loire",                    "Bureau — Location"),
    ("/locaux-commerciaux-location/indre-et-loire",         "Local commercial — Location"),
    ("/locaux-activite-entrepots-location/indre-et-loire",  "Local d'activité — Location"),
    ("/bureaux-vente/indre-et-loire",                       "Bureau — Vente"),
    ("/locaux-commerciaux-vente/indre-et-loire",            "Local commercial — Vente"),
    ("/locaux-activite-entrepots-vente/indre-et-loire",     "Local d'activité — Vente"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_seen() -> dict:
    if SEEN_JSON_PATH.exists():
        try:
            return json.loads(SEEN_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_seen(seen: dict):
    SEEN_JSON_PATH.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch(url: str, **kwargs) -> Optional[requests.Response]:
    try:
        r = SESSION.get(url, timeout=25, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"fetch {url[:80]}: {e}")
        return None


def is_new(url: str, seen: dict) -> bool:
    return url not in seen


def mark_seen(url: str, seen: dict):
    seen[url] = TODAY


def clean(el) -> str:
    if not el:
        return "N/A"
    return re.sub(r"\s+", " ", el.get_text(separator=" ", strip=True))


def abs_url(href: str, base: str) -> str:
    return href if href.startswith("http") else base + href


def _first_photo(soup, base: str) -> str:
    skip = re.compile(r"logo|icon|avatar|banner|sprite|pixel|blank|placeholder|\.svg", re.I)
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-lazy", "data-lazy-src", "data-original"):
            src = img.get(attr, "")
            if src and not skip.search(src) and not src.startswith("data:"):
                return abs_url(src, base)
    return ""


# ── Reclassification du type par analyse du titre ────────────────────────────

_KW_LOCAL = re.compile(
    r"local\s+comm|fond\s+de\s+commerce|boutique|commerce\b|restaurant|brasserie|"
    r"magasin|\bbar\b|tabac|coiffure|pharmacie|cave\b|salon\s+de|pressing|épicerie|"
    r"pizzeria|snack|agence\s+(?:immo|banc|voyages?)\b|galerie\s+(?:comm|march)",
    re.I
)
_KW_BUREAU = re.compile(
    r"\bbureau\w*\b|open[-\s]space|plateau\s+(?:de\s+)?bureau|coworking", re.I
)
_KW_ACTIVITE = re.compile(
    r"entrep[oô]t|local\s+d.activit|activit[eé]|atelier\b|hangar|stockage|"
    r"logistique|industriel", re.I
)

def _reclassify_type(listing: dict) -> dict:
    """Corrige le type en se basant sur le titre (plus fiable que la catégorie source)."""
    titre = listing.get("titre", "")
    orig  = listing.get("type", "")
    vente = "vente" in orig.lower() or bool(
        re.search(r"\bvente\b|\bvendre\b|\bcession\b", titre, re.I)
    )
    if _KW_LOCAL.search(titre):
        new = "Local commercial — Vente" if vente else "Local commercial — Location"
    elif _KW_ACTIVITE.search(titre):
        new = "Local d'activité — Vente" if vente else "Local d'activité — Location"
    elif _KW_BUREAU.search(titre) and not _KW_LOCAL.search(titre):
        new = "Bureau — Vente" if vente else "Bureau — Location"
    else:
        return listing
    if new != orig:
        log.info(f"Reclassif : '{orig}' → '{new}' | {titre[:60]}")
        return {**listing, "type": new}
    return listing


# ── Tri géographique par proximité de Tours ───────────────────────────────────

CITY_PROXIMITY: dict[str, int] = {
    "tours":                   0,
    "saint-cyr-sur-loire":     1,  "saint cyr sur loire":  1,
    "la riche":                2,  "la-riche":             2,
    "saint-pierre-des-corps":  3,  "saint pierre des corps": 3,
    "saint-avertin":           4,  "saint avertin":        4,
    "joué-lès-tours":          5,  "joue-les-tours":       5,  "joué les tours": 5,
    "fondettes":               6,
    "rochecorbon":             7,
    "chambray-lès-tours":      8,  "chambray-les-tours":   8,  "chambray les tours": 8,
    "mettray":                 9,
    "montlouis-sur-loire":    10,  "montlouis sur loire": 10,
    "ballan-miré":            11,  "ballan-mire":         11,  "ballan miré": 11,
    "veigné":                 12,  "veigne":              12,
    "vouvray":                13,
    "vernou-sur-brenne":      14,  "vernou sur brenne":   14,
    "amboise":                15,
    "sorigny":                16,
    "chinon":                 17,
}

def _city_sort_key(localisation: str) -> tuple:
    loc = re.sub(r"\b\d{5}\b", "", localisation.lower()).strip()
    loc = re.sub(r"\s+", " ", loc)
    if loc in CITY_PROXIMITY:
        return (CITY_PROXIMITY[loc], loc)
    for city, order in CITY_PROXIMITY.items():
        if loc.startswith(city) or city in loc:
            return (order, loc)
    return (999, loc)


# ── Déduplication inter-sources ───────────────────────────────────────────────

CITY_ALIASES = {
    "saint pierre des corps": "saint-pierre-des-corps",
    "saint cyr sur loire":    "saint-cyr-sur-loire",
    "chambray les tours":     "chambray-les-tours",
    "joue les tours":         "joue-les-tours",
    "la riche":               "la-riche",
}

def _norm_city(raw: str) -> str:
    s = re.sub(r"[^a-z\s-]", "", raw.lower().strip())
    s = re.sub(r"\s+", " ", s).split(" — ")[0].split(",")[0].strip()
    return CITY_ALIASES.get(s, s.split()[0] if s else "")

def _norm_surface(raw: str) -> Optional[int]:
    m = re.search(r"(\d[\d\s]*)", raw.replace("\xa0", "").replace(",", "."))
    if m:
        try:
            return int(float(m.group(1).replace(" ", "")))
        except ValueError:
            return None
    return None

def _norm_price(raw: str) -> Optional[int]:
    nums = re.findall(r"\d+", raw.replace("\xa0", "").replace(" ", ""))
    for n in nums:
        if int(n) >= 100:
            return int(n)
    return None

def _norm_type(raw: str) -> str:
    t = raw.lower().split(" — ")[0]
    if "bureau" in t:       return "bureau"
    if "activit" in t or "entrepot" in t or "entrepôt" in t: return "activite"
    if "local" in t or "commerce" in t: return "local"
    if "terrain" in t:      return "terrain"
    return t[:10]

def deduplicate(listings: list) -> list:
    buckets: dict[str, dict] = {}
    order: list[str] = []
    extras: list[dict] = []

    for l in listings:
        surf = _norm_surface(l.get("surface", ""))
        city = _norm_city(l.get("localisation", ""))
        typ  = _norm_type(l.get("type", ""))
        prix = _norm_price(l.get("prix", ""))
        if not surf or not city:
            extras.append(l)
            continue
        fp = f"{typ}_{city}_{surf}_{prix if prix else 'na'}"
        if fp in buckets:
            existing = buckets[fp]
            if l["source"] not in existing["source"]:
                existing["source"] += f" + {l['source']}"
            log.info(f"Doublon inter-sources: {fp} → {existing['source']}")
        else:
            buckets[fp] = l
            order.append(fp)

    deduped = [buckets[fp] for fp in order] + extras
    removed = len(listings) - len(deduped)
    if removed:
        log.info(f"Déduplication : {removed} doublon(s) supprimé(s)")
    return deduped


# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_geolocaux_page(url: str, label: str, seen: dict) -> list:
    r = fetch(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select(".annonce")
    links = [a["href"] for a in soup.find_all("a", href=True) if "/annonce/" in a["href"]]
    results = []
    for i in range(min(len(cards), len(links))):
        href = links[i]
        url_annonce = abs_url(href, GEOLOCAUX_BASE)
        if not is_new(url_annonce, seen):
            continue
        card = cards[i]
        titre_el = card.select_one(".title")
        prix_el  = card.select_one(".price_wrapper")
        surf_el  = card.select_one(".surf")
        pub_el   = card.select_one(".publisher")
        desc_el  = card.select_one(".accroche")
        titre = clean(titre_el)
        ref_m = re.search(r"-(\d+)\.html$", href)
        ville_m = re.search(
            r"(Tours|Amboise|Chinon|Joué|Saint-Cyr|Saint-Pierre|"
            r"Chambray|La Riche|Fondettes|Ballan|Sorigny)", titre, re.I
        )
        photo = ""
        for el in [card] + card.find_all(True, style=True):
            pm = re.search(r"url\(['\"]?([^)'\"]+)['\"]?\)", el.get("style", ""))
            if pm:
                src = pm.group(1)
                if not src.startswith("data:"):
                    photo = abs_url(src, GEOLOCAUX_BASE)
                    break
        if not photo:
            photo = _first_photo(card, GEOLOCAUX_BASE)
        listing = {
            "source": "Geolocaux",
            "type": label,
            "titre": titre,
            "localisation": ville_m.group(0) if ville_m else DEPT_NAME,
            "surface": clean(surf_el),
            "prix": clean(prix_el),
            "agence": clean(pub_el),
            "description": clean(desc_el)[:400],
            "url": url_annonce, "photo": photo,
            "reference": ref_m.group(1) if ref_m else "",
            "date": "",
        }
        results.append(listing)
        mark_seen(url_annonce, seen)
    return results


def scrape_geolocaux(seen: dict) -> list:
    results = []
    for transaction, type_bien, label, cities in GEOLOCAUX_TYPES:
        for city in cities:
            url = f"{GEOLOCAUX_BASE}/{transaction}/{type_bien}/{city}/"
            page_results = scrape_geolocaux_page(url, label, seen)
            results.extend(page_results)
            if page_results:
                log.info(f"  Geolocaux {label} {city}: {len(page_results)} nouvelles")
            time.sleep(2)
    log.info(f"Geolocaux TOTAL: {len(results)} nouvelles annonces")
    return results


def scrape_arthur_loyd(seen: dict) -> list:
    results = []
    for slug in ARTHUR_LOYD_SLUGS:
        url = f"{ARTHUR_LOYD_BASE}/{slug}/centre-val-de-loire/indre-et-loire"
        r = fetch(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        seen_hrefs = set()
        offer_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href in seen_hrefs:
                continue
            parts = [p for p in href.split("/") if p]
            if (slug.split("-")[0] in href and "indre-et-loire" in href
                    and len(parts) >= 5):
                seen_hrefs.add(href)
                offer_urls.append(abs_url(href, ARTHUR_LOYD_BASE))
        label = "Bureau — Location" if "bureau" in slug else "Terrain — Location"
        for link in offer_urls[:30]:
            if not is_new(link, seen):
                continue
            rf = fetch(link)
            mark_seen(link, seen)
            if not rf:
                continue
            fsoup = BeautifulSoup(rf.text, "html.parser")
            h1 = fsoup.find("h1")
            titre = clean(h1) if h1 else link.split("/")[-1].replace("-", " ").title()
            surface_m = re.search(r"(\d[\d\s]*)\s*m²", rf.text)
            prix_m = re.search(r"D[eè]s\s*([\d\s]+)\s*€", rf.text)
            if not prix_m:
                prix_m = re.search(r"([\d\s]{3,})\s*€", rf.text)
            meta = fsoup.find("meta", {"name": "description"})
            parts = link.split("/")
            ville = parts[-2].replace("-", " ").title() if len(parts) >= 2 else DEPT_NAME
            photo = _first_photo(fsoup, ARTHUR_LOYD_BASE)
            listing = {
                "source": "Arthur Loyd",
                "type": label,
                "titre": titre,
                "localisation": ville,
                "surface": surface_m.group(0).strip() if surface_m else "N/A",
                "prix": prix_m.group(0).strip() if prix_m else "N/A",
                "agence": "Arthur Loyd",
                "description": meta["content"][:400] if meta and meta.get("content") else "N/A",
                "url": link, "photo": photo,
                "reference": re.search(r"ref(\d+)", link, re.I).group(1)
                             if re.search(r"ref(\d+)", link, re.I) else "",
                "date": "",
            }
            results.append(listing)
            time.sleep(1.5)
        time.sleep(2)
    log.info(f"Arthur Loyd: {len(results)} nouvelles annonces")
    return results


def scrape_weadvisor(seen: dict) -> list:
    results = []
    for dept_path, label in WEADVISOR_SEARCHES:
        type_prefix = dept_path.split("/")[1]  # ex: "bureaux-location"
        r = fetch(WEADVISOR_BASE + dept_path)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        city_links = list(dict.fromkeys(
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].startswith(dept_path + "/")
        ))
        for city_href in city_links:
            city_r = fetch(WEADVISOR_BASE + city_href)
            if not city_r:
                time.sleep(1)
                continue
            city_soup = BeautifulSoup(city_r.text, "html.parser")
            annonce_links = list(dict.fromkeys(
                abs_url(a["href"], WEADVISOR_BASE)
                for a in city_soup.find_all("a", href=True)
                if re.match(rf"^/{re.escape(type_prefix)}/[^/]+/[^/]+", a["href"])
                and "indre-et-loire" not in a["href"]
            ))
            for link in annonce_links:
                if not is_new(link, seen):
                    continue
                rf = fetch(link)
                mark_seen(link, seen)
                if not rf:
                    continue
                fsoup = BeautifulSoup(rf.text, "html.parser")
                h1 = fsoup.find("h1")
                surf_m = re.search(r"(\d[\d\s]*)\s*m²", rf.text)
                prix_m = re.search(r"([\d\s]{4,})\s*€", rf.text)
                city_name = city_href.split("/")[-1].replace("-", " ").title()
                photo = _first_photo(fsoup, WEADVISOR_BASE)
                listing = {
                    "source": "Weadvisor",
                    "type": label,
                    "titre": clean(h1) if h1 else "N/A",
                    "localisation": city_name,
                    "surface": surf_m.group(0).strip() if surf_m else "N/A",
                    "prix": prix_m.group(0).strip() if prix_m else "N/A",
                    "agence": "Weadvisor",
                    "description": "N/A",
                    "url": link, "photo": photo,
                    "reference": "",
                    "date": "",
                }
                results.append(listing)
                time.sleep(1.5)
            time.sleep(1)
        time.sleep(2)
    log.info(f"Weadvisor: {len(results)} nouvelles annonces")
    return results


# ── Scraper LeBonCoin (curl_cffi + NEXT_DATA — fonctionne depuis GitHub Actions) ──
LBC_CAT_LABELS = {
    "8":  "Bureau / Local commercial",
    "9":  "Local d'activité",
    "13": "Bureau / Local commercial",
}


def scrape_leboncoin(seen: dict) -> list:
    """curl_cffi imite le TLS fingerprint de Chrome → passe DataDome même depuis GitHub Actions."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        log.warning("LeBonCoin : curl_cffi non installé — pip install curl_cffi")
        return []

    results = []
    all_ads = []

    for page_num in range(1, 6):   # 5 pages × 35 = ~175 annonces récentes
        try:
            r = cffi_requests.get(
                f"https://www.leboncoin.fr/recherche?category=13&locations=d_{DEPT}&page={page_num}",
                impersonate="chrome124",
                headers={"Accept-Language": "fr-FR,fr;q=0.9"},
                timeout=25,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning(f"LeBonCoin page {page_num} : {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
        if not nd:
            log.warning(f"LeBonCoin : NEXT_DATA absent page {page_num}")
            break

        try:
            data = json.loads(nd.string)
            ads = (data.get("props", {}).get("pageProps", {})
                   .get("searchData", {}).get("ads", []))
        except Exception as e:
            log.warning(f"LeBonCoin JSON page {page_num} : {e}")
            break

        if not ads:
            break
        all_ads.extend(ads)
        time.sleep(1)

    log.info(f"LeBonCoin : {len(all_ads)} annonces bureaux/commerces en dept {DEPT}")
    for ad in all_ads:
        url = ad.get("url", "")
        if not url or not is_new(url, seen):
            continue
        attrs = {a["key"]: a.get("value_label", (a.get("values") or [""])[0])
                 for a in ad.get("attributes", []) if "key" in a}
        prix_raw = ad.get("price", [None])[0] if ad.get("price") else None
        cat_id = str(ad.get("category_id", "13"))
        imgs  = ad.get("images", {})
        photo = (imgs.get("thumb_url") or
                 next(iter(imgs.get("urls_thumb", [])), "") or
                 next(iter(imgs.get("urls", [])), "") or "")
        listing = {
            "source":       "LeBonCoin",
            "type":         LBC_CAT_LABELS.get(cat_id, "Bureau / Local commercial"),
            "titre":        ad.get("subject", "N/A"),
            "localisation": ad.get("location", {}).get("city", DEPT_NAME),
            "surface":      attrs.get("square", "N/A"),
            "prix":         f"{prix_raw:,} €".replace(",", " ") if prix_raw else "N/A",
            "agence":       ad.get("owner", {}).get("name", "Particulier"),
            "description":  (ad.get("body") or "")[:400],
            "url":          url, "photo": photo,
            "reference":    str(ad.get("list_id", "")),
            "date":         (ad.get("first_publication_date") or "")[:10],
        }
        results.append(listing)
        mark_seen(url, seen)

    log.info(f"LeBonCoin : {len(results)} nouvelles annonces")
    return results


# ── Scraper Im Valoris ────────────────────────────────────────────────────────

def scrape_imvaloris(seen: dict) -> list:
    results = []
    r = fetch(IMVALORIS_LIST)
    if not r:
        log.warning("Im Valoris : page inaccessible")
        return results
    soup = BeautifulSoup(r.text, "html.parser")
    cards = [c for c in soup.select("[class*=annonce]")
             if c.find("a", href=True) and IMVALORIS_KEYWORDS.search(c.get_text())]
    log.info(f"Im Valoris : {len(cards)} annonces commerciales trouvées")
    for card in cards:
        link_tag = card.find("a", href=True)
        url = abs_url(link_tag["href"], IMVALORIS_BASE)
        ref_m = re.search(r"ref=([^&]+)", url)
        ref = ref_m.group(1) if ref_m else ""
        uid = f"imvaloris_{ref}" if ref else url
        if not is_new(uid, seen):
            continue
        rf = fetch(url)
        mark_seen(uid, seen)
        if not rf:
            continue
        fsoup = BeautifulSoup(rf.text, "html.parser")
        h1 = fsoup.find("h1")
        surf_m = re.search(r"(\d[\d\s,\.]*)\s*m²", rf.text)
        prix_m = re.search(r"([\d\s]{3,})\s*€", rf.text)
        txt = card.get_text(separator=" ", strip=True)
        type_bien = "Local commercial — Location"
        if "vente" in txt.lower() or "achat" in txt.lower():
            type_bien = "Local commercial — Vente"
        elif "bureau" in txt.lower():
            type_bien = "Bureau — Location"
        photo = _first_photo(fsoup, IMVALORIS_BASE)
        listing = {
            "source": "Im Valoris",
            "type": type_bien,
            "titre": clean(h1) if h1 else txt[:80],
            "localisation": "Tours",
            "surface": surf_m.group(0).strip() if surf_m else "N/A",
            "prix": prix_m.group(0).strip() if prix_m else "N/A",
            "agence": "Im Valoris",
            "description": txt[:400],
            "url": url, "photo": photo,
            "reference": ref, "date": "",
        }
        results.append(listing)
        time.sleep(1.5)
    log.info(f"Im Valoris : {len(results)} nouvelles annonces")
    return results




# ── Scraper Equimmox (Playwright) ─────────────────────────────────────────────

async def _scrape_equimmox_async(seen: dict) -> list:
    if not EQUIMMOX_EMAIL or not EQUIMMOX_PASS:
        log.warning("Equimmox : identifiants manquants (EQUIMMOX_EMAIL / EQUIMMOX_PASS)")
        return []
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(EQUIMMOX_LOGIN, timeout=30000)
            await page.fill('input[type="email"]', EQUIMMOX_EMAIL)
            await page.fill('input[type="password"]', EQUIMMOX_PASS)
            await page.click('button:has-text("Se connecter")')
            await page.wait_for_url("https://app.equimmox.com/**", timeout=20000)

            await page.goto(EQUIMMOX_SEARCH, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page.wait_for_timeout(4000)  # Bubble.io SPA : attendre le rendu

            # Scroll pour charger toutes les annonces
            for _ in range(6):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

            cards = await page.eval_on_selector_all(
                ".bubble-element.group-item",
                "els => els.map(el => el.innerText.trim())"
            )
            log.info(f"Equimmox : {len(cards)} cartes trouvées")

            for text in cards:
                parts = [p.strip() for p in text.split("\n") if p.strip()]
                if len(parts) < 5:
                    continue
                # Format confirmé : prix_m2 / date / type / ville / prix / surface
                prix_m2  = parts[0]
                date_pub = parts[1]
                type_bien = parts[2]
                city     = parts[3]
                prix     = parts[4]
                surface  = parts[5] if len(parts) > 5 else "N/A"

                uid = f"equimmox_{type_bien}_{city}_{surface}_{prix}".replace(" ", "_")
                if not is_new(uid, seen):
                    continue

                listing = {
                    "source": "Equimmox",
                    "type": type_bien,
                    "titre": f"{type_bien} — {city}",
                    "localisation": city,
                    "surface": surface,
                    "prix": prix,
                    "agence": "Equimmox",
                    "description": f"{prix_m2} | Publié le {date_pub}",
                    "url": EQUIMMOX_SEARCH, "photo": "",
                    "reference": "",
                    "date": date_pub,
                }
                results.append(listing)
                mark_seen(uid, seen)

        except Exception as e:
            log.error(f"Equimmox : erreur scraping — {e}")
        finally:
            await browser.close()

    log.info(f"Equimmox : {len(results)} nouvelles annonces")
    return results


def scrape_equimmox(seen: dict) -> list:
    return asyncio.run(_scrape_equimmox_async(seen))


# ── Rapport HTML ──────────────────────────────────────────────────────────────

SOURCE_COLORS = {
    "Geolocaux":   "#2d7a3a",
    "Arthur Loyd": "#b35a00",
    "Weadvisor":   "#5c3d9e",
    "LeBonCoin":   "#c0392b",
    "Equimmox":    "#0a6eb4",
    "Im Valoris":  "#7a3a6e",
}

TYPE_ORDER = [
    "Local commercial — Location",
    "Local commercial — Vente",
    "Bureau — Location",
    "Bureau — Vente",
    "Local d'activité — Location",
    "Local d'activité — Vente",
]

TYPE_COLORS = {
    "Local commercial — Location": "#c0392b",
    "Local commercial — Vente":    "#7b241c",
    "Bureau — Location":           "#0a6eb4",
    "Bureau — Vente":              "#1a5276",
    "Local d'activité — Location": "#2d7a3a",
    "Local d'activité — Vente":    "#1e5724",
}

def _type_bucket(l: dict) -> str:
    t = l.get("type", "")
    norm = _norm_type(t)
    vente = "vente" in t.lower() or "achat" in t.lower()
    if norm in ("local", "commerce"):
        return "Local commercial — Vente" if vente else "Local commercial — Location"
    if norm == "bureau":
        return "Bureau — Vente" if vente else "Bureau — Location"
    if norm == "activite":
        return "Local d'activité — Vente" if vente else "Local d'activité — Location"
    return "Autres"

def _card_html(l: dict) -> str:
    e = _html.escape
    color = SOURCE_COLORS.get(l["source"].split(" + ")[0], "#555")
    photo = l.get("photo", "")
    if photo:
        photo_td = (
            f'<td width="200" valign="top" style="padding:0;min-width:200px;">'
            f'<img src="{e(photo)}" width="200" '
            f'style="display:block;width:200px;height:auto;" alt="photo"></td>'
        )
    else:
        photo_td = (
            f'<td width="200" valign="top" style="padding:0;min-width:200px;">'
            f'<div style="width:200px;height:130px;background:#e8edf2;'
            f'text-align:center;color:#aaa;font-size:12px;font-family:Arial;padding-top:50px;">'
            f'Pas de photo</div></td>'
        )
    transaction = "Vente" if "Vente" in l["type"] else "Location"
    trans_color = "#b35a00" if transaction == "Vente" else "#0a6eb4"
    type_short  = l["type"].split(" — ")[0]
    type_badge_colors = {
        "Local commercial": "#c0392b",
        "Bureau":           "#1a5276",
        "Local d'activité": "#1e5724",
        "Terrain":          "#6c3483",
    }
    type_badge_color = type_badge_colors.get(type_short, "#555")
    desc = e(l.get("description", "N/A") or "N/A")
    desc_row = f'<tr><td colspan="2" style="padding:6px 0 0;font-size:12px;color:#666;">{desc[:200]}</td></tr>' if desc and desc != "N/A" else ""
    return f'''
<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;margin-bottom:14px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,0.10);">
<tr>
  {photo_td}
  <td valign="top" style="padding:14px 16px;">
    <table cellpadding="0" cellspacing="0" width="100%"><tr>
      <td>
      <span style="background:{color};color:#fff;padding:2px 9px;border-radius:10px;font-size:11px;font-family:Arial;font-weight:bold;">{e(l["source"])}</span>&nbsp;
      <span style="background:{type_badge_color};color:#fff;padding:2px 9px;border-radius:10px;font-size:11px;font-family:Arial;font-weight:bold;">{e(type_short)}</span>&nbsp;
      <span style="background:{trans_color};color:#fff;padding:2px 9px;border-radius:10px;font-size:11px;font-family:Arial;">{e(transaction)}</span></td>
      <td align="right" style="font-size:11px;color:#999;font-family:Arial;">{e(l.get("date","") or "")}</td>
    </tr></table>
    <div style="font-size:15px;font-weight:bold;color:#1a1a1a;font-family:Arial;margin:8px 0 6px;line-height:1.3;">{e(l["titre"])}</div>
    <table cellpadding="3" cellspacing="0" style="font-size:13px;font-family:Arial;color:#444;width:100%;">
      <tr><td style="color:#888;white-space:nowrap;padding-right:10px;">Localisation</td><td><b>{e(l["localisation"])}</b></td></tr>
      <tr><td style="color:#888;white-space:nowrap;">Surface</td><td>{e(l["surface"])}</td></tr>
      <tr><td style="color:#888;white-space:nowrap;">Prix / Loyer</td><td><b style="color:#1e3a5f;">{e(l["prix"])}</b></td></tr>
      <tr><td style="color:#888;white-space:nowrap;">Agence</td><td>{e(l["agence"])}</td></tr>
      {desc_row}
    </table>
    <a href="{e(l['url'])}" style="display:inline-block;background:#1e3a5f;color:#ffffff;padding:7px 16px;border-radius:4px;text-decoration:none;font-size:13px;font-family:Arial;margin-top:10px;">Voir l&apos;annonce &rarr;</a>
  </td>
</tr>
</table>'''


def build_html_report(all_listings: list, inaccessible: list) -> str:
    e = _html.escape
    by_type: dict = {}
    for l in all_listings:
        by_type.setdefault(_type_bucket(l), []).append(l)

    summary_rows = ""
    for t in TYPE_ORDER:
        if t in by_type:
            summary_rows += (
                f'<tr><td style="padding:3px 12px 3px 0;color:#ccd;">{e(t)}</td>'
                f'<td style="padding:3px 0;font-weight:bold;">{len(by_type[t])}</td></tr>'
            )
    if "Autres" in by_type:
        summary_rows += (
            f'<tr><td style="padding:3px 12px 3px 0;color:#ccd;">Autres</td>'
            f'<td style="padding:3px 0;font-weight:bold;">{len(by_type["Autres"])}</td></tr>'
        )

    err_banner = (
        f'<div style="background:#fdecea;border-left:4px solid #c0392b;padding:10px 16px;'
        f'margin-bottom:16px;border-radius:4px;font-family:Arial;font-size:13px;color:#922;">'
        f'Erreurs : {e(", ".join(inaccessible))}</div>'
    ) if inaccessible else ""

    body_parts = []
    displayed = [t for t in TYPE_ORDER if t in by_type]
    if "Autres" in by_type:
        displayed.append("Autres")

    for type_label in displayed:
        listings = sorted(by_type[type_label],
                          key=lambda l: _city_sort_key(l.get("localisation", "")))
        color = TYPE_COLORS.get(type_label, "#555")
        body_parts.append(
            f'<div style="font-size:16px;font-weight:bold;color:#fff;background:{color};'
            f'padding:8px 16px;border-radius:6px;margin:20px 0 10px;font-family:Arial;">'
            f'{e(type_label)} &mdash; {len(listings)} annonce(s)</div>'
        )
        for l in listings:
            body_parts.append(_card_html(l))

    if not all_listings:
        body_parts.append('<p style="font-family:Arial;color:#666;font-style:italic;">Aucune nouvelle annonce aujourd\'hui.</p>')

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1e3a5f;">
<tr><td align="center" style="padding:28px 20px;">
  <div style="font-size:22px;font-weight:bold;color:#ffffff;font-family:Arial;">Veille Immobilière Commerciale</div>
  <div style="font-size:14px;color:#aac4e8;font-family:Arial;margin-top:6px;">Indre-et-Loire (37) &bull; {e(TODAY)} &bull; {len(all_listings)} nouvelle(s) annonce(s)</div>
</td></tr>
</table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f9fc;border-bottom:1px solid #dde;">
<tr><td style="padding:16px 24px;">
  <table cellpadding="0" cellspacing="0" style="font-size:13px;font-family:Arial;color:#1e3a5f;">{summary_rows}</table>
</td></tr>
</table>
<div style="max-width:680px;margin:0 auto;padding:20px 16px;">
  {err_banner}
  {"".join(body_parts)}
</div>
</body></html>"""


def build_report(all_listings: list, inaccessible: list) -> str:
    """Version Markdown pour le fichier .md sauvegardé dans le dépôt."""
    by_source: dict = {}
    for l in all_listings:
        by_source.setdefault(l["source"], []).append(l)
    lines = [f"# Veille {TODAY} — {len(all_listings)} annonces", ""]
    for source, listings in sorted(by_source.items()):
        lines.append(f"## {source} ({len(listings)})")
        for l in listings:
            lines += [
                f"### {l['type']} — {l['localisation']}",
                f"**{l['titre']}**  ",
                f"Surface : {l['surface']} | Prix : {l['prix']} | Agence : {l['agence']}  ",
                f"URL : {l['url']}", "",
            ]
    return "\n".join(lines)


# ── Email SMTP Office 365 ─────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    plain = re.sub(r"<[^>]+>", " ", html_body)
    plain = re.sub(r"\s+", " ", plain).strip()
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.office365.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())
        log.info(f"Email envoyé à {EMAIL_TO}")
    except Exception as e:
        log.error(f"Erreur envoi email : {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"=== Démarrage veille {TODAY} ===")
    seen = load_seen()
    all_listings = []
    inaccessible = []

    for name, fn in [
        ("Geolocaux",   scrape_geolocaux),
        ("Arthur Loyd", scrape_arthur_loyd),
        ("Weadvisor",   scrape_weadvisor),
        ("Im Valoris",  scrape_imvaloris),
        ("LeBonCoin",   scrape_leboncoin),
        ("Equimmox",    scrape_equimmox),
    ]:
        try:
            res = fn(seen)
            all_listings.extend(res)
        except Exception as e:
            log.error(f"Erreur {name}: {e}", exc_info=True)
            inaccessible.append(name)

    save_seen(seen)

    all_listings = [_reclassify_type(l) for l in all_listings]
    all_listings = deduplicate(all_listings)
    Path(f"veille_{TODAY}.md").write_text(
        build_report(all_listings, inaccessible), encoding="utf-8"
    )
    log.info(f"Rapport sauvegardé : veille_{TODAY}.md")

    if all_listings or inaccessible:
        send_email(
            f"Veille Immo 37 — {TODAY} — {len(all_listings)} nouvelles annonces",
            build_html_report(all_listings, inaccessible),
        )
    else:
        log.info("Aucune nouvelle annonce — email non envoyé (Mac a probablement déjà traité)")
    log.info(f"=== Fin : {len(all_listings)} nouvelles annonces ===")


if __name__ == "__main__":
    main()
