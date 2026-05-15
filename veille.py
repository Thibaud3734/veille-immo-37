#!/usr/bin/env python3
"""
Veille immobilière commerciale — Indre-et-Loire (37)
Version cloud : email via SMTP Office 365
"""

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

# ── Configuration ─────────────────────────────────────────────────────────────
TODAY = datetime.now().strftime("%Y-%m-%d")
DEPT = "37"
DEPT_NAME = "Indre-et-Loire"

EMAIL_TO   = os.environ["EMAIL_TO"]    # t.segeon@la-ie.fr
SMTP_USER  = os.environ["SMTP_USER"]   # compte expéditeur Office 365
SMTP_PASS  = os.environ["SMTP_PASS"]   # mot de passe ou app password

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
    "Accept-Encoding": "gzip, deflate, br",
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
    "tours-37000",
    "joue-les-tours-37300",
    "saint-pierre-des-corps-37700",
    "saint-cyr-sur-loire-37540",
    "chambray-les-tours-37170",
    "la-riche-37520",
    "fondettes-37230",
    "ballan-mire-37510",
    "amboise-37400",
    "chinon-37500",
]

GEOLOCAUX_TYPES = [
    ("location", "bureau",           "Bureau — Location"),
    ("location", "local-commercial", "Local commercial — Location"),
    ("location", "entrepot",         "Local d'activité — Location"),
    ("vente",    "bureau",           "Bureau — Vente"),
    ("vente",    "local-commercial", "Local commercial — Vente"),
    ("vente",    "entrepot",         "Local d'activité — Vente"),
]

ARTHUR_LOYD_SLUGS = ["bureau-location", "terrain-location"]

GEOLOCAUX_BASE   = "https://www.geolocaux.com"
ARTHUR_LOYD_BASE = "https://www.arthur-loyd.com"
WEADVISOR_BASE   = "https://www.weadvisor.fr"

WEADVISOR_SEARCHES = [
    ("/locaux-commerciaux-location/indre-et-loire", "Local commercial — Location"),
    ("/bureaux-location/indre-et-loire",            "Bureau — Location"),
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
        listing = {
            "source": "Geolocaux",
            "type": label,
            "titre": titre,
            "localisation": ville_m.group(0) if ville_m else DEPT_NAME,
            "surface": clean(surf_el),
            "prix": clean(prix_el),
            "agence": clean(pub_el),
            "description": clean(desc_el)[:400],
            "url": url_annonce,
            "reference": ref_m.group(1) if ref_m else "",
            "date": "",
        }
        results.append(listing)
        mark_seen(url_annonce, seen)
    return results


def scrape_geolocaux(seen: dict) -> list:
    results = []
    for transaction, type_bien, label in GEOLOCAUX_TYPES:
        for city in CITIES_37:
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
            listing = {
                "source": "Arthur Loyd",
                "type": label,
                "titre": titre,
                "localisation": ville,
                "surface": surface_m.group(0).strip() if surface_m else "N/A",
                "prix": prix_m.group(0).strip() if prix_m else "N/A",
                "agence": "Arthur Loyd",
                "description": meta["content"][:400] if meta and meta.get("content") else "N/A",
                "url": link,
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
    for path, label in WEADVISOR_SEARCHES:
        r = fetch(WEADVISOR_BASE + path)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        offer_links = list(dict.fromkeys(
            abs_url(a["href"], WEADVISOR_BASE)
            for a in soup.find_all("a", href=True)
            if any(x in a["href"] for x in ["/annonce/", "/offre/", "/bien/"])
            and len(a["href"]) > 10
        ))
        for link in offer_links[:20]:
            if not is_new(link, seen):
                continue
            rf = fetch(link)
            mark_seen(link, seen)
            if not rf:
                continue
            fsoup = BeautifulSoup(rf.text, "html.parser")
            h1 = fsoup.find("h1")
            surf_m = re.search(r"(\d[\d\s]*)\s*m²", rf.text)
            prix_m = re.search(r"([\d\s]{3,})\s*€", rf.text)
            meta = fsoup.find("meta", {"name": "description"})
            listing = {
                "source": "Weadvisor",
                "type": label,
                "titre": clean(h1) if h1 else "N/A",
                "localisation": DEPT_NAME,
                "surface": surf_m.group(0).strip() if surf_m else "N/A",
                "prix": prix_m.group(0).strip() if prix_m else "N/A",
                "agence": "Weadvisor",
                "description": meta["content"][:400] if meta and meta.get("content") else "N/A",
                "url": link,
                "reference": "",
                "date": "",
            }
            results.append(listing)
            time.sleep(1.5)
        time.sleep(2)
    log.info(f"Weadvisor: {len(results)} nouvelles annonces")
    return results


# ── Rapport ───────────────────────────────────────────────────────────────────

def build_report(all_listings: list, inaccessible: list) -> str:
    by_source = {}
    type_counts = {}
    for l in all_listings:
        by_source.setdefault(l["source"], []).append(l)
        short_type = l["type"].split(" — ")[0]
        type_counts[short_type] = type_counts.get(short_type, 0) + 1

    lines = [
        f"# Veille Immobilière Commerciale — {DEPT_NAME} ({DEPT})",
        f"## {TODAY}",
        "",
        "---",
        "",
        "## Résumé",
        f"- **Nouvelles annonces :** {len(all_listings)}",
    ]
    for t, n in sorted(type_counts.items()):
        lines.append(f"  - {t} : {n}")
    if by_source:
        lines.append(f"- **Sources actives :** {', '.join(by_source.keys())}")
    if inaccessible:
        lines.append(f"- **Erreurs :** {', '.join(inaccessible)}")
    lines += ["", "---", ""]

    if not all_listings:
        lines.append("_Aucune nouvelle annonce détectée aujourd'hui._")
    else:
        for source, listings in sorted(by_source.items()):
            lines += [f"## {source}  ({len(listings)} nouvelle(s))", ""]
            for l in listings:
                lines += [
                    f"### {l['type']} — {l['localisation']}",
                    f"**{l['titre']}**",
                    "",
                    f"| Champ | Valeur |",
                    f"|-------|--------|",
                    f"| Surface | {l['surface']} |",
                    f"| Prix / Loyer | {l['prix']} |",
                    f"| Agence | {l['agence']} |",
                    f"| Référence | {l['reference'] or 'N/A'} |",
                    f"| Date publication | {l['date'] or 'N/A'} |",
                    "",
                    f"**Description :** {l['description']}",
                    "",
                    f"Lien : {l['url']}",
                    "",
                    "---",
                    "",
                ]
    return "\n".join(lines)


# ── Email SMTP Office 365 ─────────────────────────────────────────────────────

def send_email(subject: str, body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(body, "plain", "utf-8"))
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
    ]:
        try:
            res = fn(seen)
            all_listings.extend(res)
        except Exception as e:
            log.error(f"Erreur {name}: {e}", exc_info=True)
            inaccessible.append(name)

    save_seen(seen)

    report = build_report(all_listings, inaccessible)
    Path(f"veille_{TODAY}.md").write_text(report, encoding="utf-8")
    log.info(f"Rapport sauvegardé : veille_{TODAY}.md")

    send_email(
        f"Veille Immo 37 — {TODAY} — {len(all_listings)} nouvelles annonces",
        report,
    )
    log.info(f"=== Fin : {len(all_listings)} nouvelles annonces ===")


if __name__ == "__main__":
    main()
