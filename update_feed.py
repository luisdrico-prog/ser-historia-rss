#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SER Historia — RSS histórico completo para Feedly (v3 acumulativo)

Fuente: Web oficial de Nacho Ares (nachoares.com)

Funcionamiento:
- Descubre el programa más reciente desde /ser-historia/
- Recorre hacia atrás mediante "Programa anterior"
- Conserva catalog.json entre ejecuciones
- Extrae número, título, fecha, descripción, imagen y enlace/reproductor iVoox
- Mantiene los programas recientes
- Añade históricos de forma ACUMULATIVA
- Los históricos ya expuestos NO se retiran
- Al finalizar el backfill, feed.xml contiene todo el catálogo disponible
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://nachoares.com"
INDEX_URL = f"{BASE}/ser-historia/"

CATALOG_FILE = Path("catalog.json")
STATE_FILE = Path("backfill_state.json")
FEED_FILE = Path("feed.xml")

RECENT_KEEP = int(os.getenv("RECENT_KEEP", "75"))
BACKFILL_BATCH = int(os.getenv("BACKFILL_BATCH", "250"))
BACKFILL_HOLD_HOURS = int(os.getenv("BACKFILL_HOLD_HOURS", "6"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.45"))
MAX_INITIAL = int(os.getenv("MAX_INITIAL", "1500"))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (compatible; SerHistoriaRSS/3.0; "
        "+https://github.com/luisdrico-prog/ser-historia-rss)"
    )
})


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, tries: int = 4) -> str:
    last = None
    for attempt in range(tries):
        try:
            r = SESSION.get(url, timeout=35)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            wait = 2 ** attempt
            log(f"  Aviso: fallo al cargar {url}: {exc}. Reintento en {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"No se pudo cargar {url}: {last}")


def canonical_episode_url(href: str | None) -> str | None:
    if not href:
        return None

    u = urljoin(BASE, href)
    if "/ser_historia/" not in u:
        return None

    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/") + "/"


def discover_latest() -> str:
    text = fetch(INDEX_URL)
    soup = BeautifulSoup(text, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        u = canonical_episode_url(a.get("href"))
        if not u:
            continue

        m = re.search(r"(?:ser-historia-|especial-)(\d+)", u, re.I)
        if not m:
            label = " ".join(a.stripped_strings)
            m = re.search(r"\b(?:SER\s+Historia\s+)?(\d{1,4})\b", label, re.I)

        if m:
            candidates.append((int(m.group(1)), u))

    if not candidates:
        raise RuntimeError("No he podido localizar el programa más reciente.")

    candidates.sort(reverse=True)
    return candidates[0][1]


DATE_RE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")
NUM_RE = re.compile(r"\bSER\s+Historia\s+(\d+)\b", re.I)


def clean_title(raw: str) -> str:
    raw = html.unescape(raw or "").strip()
    raw = re.sub(
        r"\s+-\s+Web Oficial de Nacho Ares.*$",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(
        r"\s+\|\s+Web Oficial de Nacho Ares.*$",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip(" -|")


def get_meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return tag["content"].strip()

        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()

    return ""


def parse_episode(url: str) -> tuple[dict, str | None]:
    text = fetch(url)
    soup = BeautifulSoup(text, "html.parser")

    og_title = get_meta(soup, "og:title", "twitter:title")
    if not og_title and soup.title:
        og_title = soup.title.get_text(" ", strip=True)

    title = clean_title(og_title)

    page_text = soup.get_text(" ", strip=True)

    num_match = NUM_RE.search(title) or NUM_RE.search(page_text)
    episode_num = int(num_match.group(1)) if num_match else None

    date_match = DATE_RE.search(title) or DATE_RE.search(page_text)
    published = None
    if date_match:
        d, m, y = map(int, date_match.groups())
        try:
            published = datetime(y, m, d, 8, 0, tzinfo=timezone.utc)
        except ValueError:
            published = None

    description = get_meta(soup, "og:description", "description")
    if not description:
        for p in soup.find_all("p"):
            txt = " ".join(p.stripped_strings)
            if len(txt) >= 80:
                description = txt
                break

    description = re.sub(
        r"\s+",
        " ",
        html.unescape(description or ""),
    ).strip()

    image = get_meta(soup, "og:image", "twitter:image")

    ivoox_player = ""
    ivoox_id = ""

    for iframe in soup.find_all("iframe", src=True):
        src = urljoin(url, iframe["src"])
        if "ivoox.com" in src and "player" in src:
            ivoox_player = src
            m = re.search(r"player_ej_(\d+)", src)
            if m:
                ivoox_id = m.group(1)
            break

    ivoox_url = f"https://go.ivoox.com/rf/{ivoox_id}" if ivoox_id else ""

    prev_url = None

    for a in soup.find_all("a", href=True):
        label = " ".join(a.stripped_strings).lower()
        if "programa anterior" in label:
            prev_url = canonical_episode_url(a["href"])
            if prev_url:
                break

    # Respaldo por numeración si cambia la plantilla.
    if not prev_url and episode_num:
        candidates = []

        for a in soup.find_all("a", href=True):
            u = canonical_episode_url(a["href"])
            if not u:
                continue

            m = re.search(r"(?:ser-historia-|especial-)(\d+)", u, re.I)

            if not m:
                label = " ".join(a.stripped_strings)
                m = re.search(
                    r"\b(?:SER\s+Historia\s+)?(\d{1,4})\b",
                    label,
                    re.I,
                )

            if m:
                n = int(m.group(1))
                if n < episode_num:
                    candidates.append((n, u))

        if candidates:
            candidates.sort(reverse=True)
            prev_url = candidates[0][1]

    guid = (
        f"ser-historia:{episode_num}"
        if episode_num is not None
        else hashlib.sha1(url.encode()).hexdigest()
    )

    item = {
        "guid": guid,
        "number": episode_num,
        "title": title or (
            f"SER Historia {episode_num}"
            if episode_num is not None
            else url
        ),
        "url": url,
        "published": published.isoformat() if published else None,
        "description": description,
        "image": image,
        "ivoox_player": ivoox_player,
        "ivoox_id": ivoox_id,
        "ivoox_url": ivoox_url,
        "seen_at": datetime.now(timezone.utc).isoformat(),
    }

    return item, prev_url


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def item_key(item: dict) -> str:
    if item.get("number") is not None:
        return f"n:{item['number']}"

    return f"u:{item.get('url', '')}"


def crawl_catalog() -> list[dict]:
    old = load_json(CATALOG_FILE, [])

    old_by_key = {
        item_key(x): x
        for x in old
        if isinstance(x, dict)
    }

    old_urls = {
        x.get("url")
        for x in old
        if isinstance(x, dict) and x.get("url")
    }

    old_numbers = [
        x.get("number")
        for x in old
        if isinstance(x, dict)
        and isinstance(x.get("number"), int)
    ]

    oldest_known_number = min(old_numbers) if old_numbers else None
    archive_complete = oldest_known_number == 1

    latest = discover_latest()
    log(f"Programa más reciente detectado: {latest}")

    url = latest
    visited = set()
    added = 0
    refreshed = 0
    total_requests = 0
    hit_known = False

    while url and url not in visited and total_requests < MAX_INITIAL:
        visited.add(url)
        total_requests += 1

        item, prev_url = parse_episode(url)
        k = item_key(item)

        if k in old_by_key or url in old_urls:
            if not hit_known:
                old_by_key[k] = {
                    **old_by_key.get(k, {}),
                    **item,
                }
                refreshed += 1

            hit_known = True

            # Si ya tenemos del último al nº 1, basta con refrescar el borde.
            if old and archive_complete:
                break

        else:
            old_by_key[k] = item
            added += 1
            n = item.get("number")
            log(f"  + SER Historia {n if n is not None else '?'}")

        url = prev_url

        if url:
            time.sleep(REQUEST_DELAY)

    catalog = list(old_by_key.values())

    def sort_key(x):
        n = x.get("number")
        return (
            n is not None,
            n if n is not None else -1,
        )

    catalog.sort(key=sort_key, reverse=True)

    log(
        f"Catálogo: {len(catalog)} programas | "
        f"nuevos: {added} | refrescados: {refreshed}"
    )

    return catalog


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return (
            dt
            if dt.tzinfo
            else dt.replace(tzinfo=timezone.utc)
        )
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def xml_escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def cdata(value: str) -> str:
    value = (value or "").replace(
        "]]>",
        "]]]]><![CDATA[>",
    )
    return f"<![CDATA[{value}]]>"


def choose_feed_items(catalog: list[dict]) -> list[dict]:
    """
    Backfill acumulativo para Feedly.

    - RECENT_KEEP programas recientes permanecen siempre.
    - Cada BACKFILL_HOLD_HOURS se añaden BACKFILL_BATCH históricos.
    - Los históricos ya expuestos NO se eliminan del RSS.
    - Al alcanzar el final, feed.xml contiene todo el catálogo.
    """
    if not catalog:
        return []

    ordered = sorted(
        catalog,
        key=lambda x: (
            x.get("number")
            if x.get("number") is not None
            else -1,
            parse_dt(x.get("published")).timestamp(),
        ),
        reverse=True,
    )

    recent = ordered[:RECENT_KEEP]
    historical = ordered[RECENT_KEEP:]

    state = load_json(STATE_FILE, {})
    now = datetime.now(timezone.utc)

    exposed = state.get("exposed_historical")

    # Migración automática desde el antiguo estado rotatorio.
    if exposed is None:
        exposed = min(BACKFILL_BATCH, len(historical))
        last_growth = now
    else:
        exposed = max(
            0,
            min(int(exposed), len(historical)),
        )
        last_growth = parse_dt(state.get("last_growth"))

    if exposed < len(historical):
        if (
            not state
            or now - last_growth
            >= timedelta(hours=BACKFILL_HOLD_HOURS)
        ):
            exposed = min(
                len(historical),
                exposed + BACKFILL_BATCH,
            )
            last_growth = now

    state = {
        "mode": "cumulative",
        "exposed_historical": exposed,
        "last_growth": last_growth.isoformat(),
        "recent_keep": RECENT_KEEP,
        "backfill_batch": BACKFILL_BATCH,
        "backfill_hold_hours": BACKFILL_HOLD_HOURS,
        "historical_total": len(historical),
        "total_exposed": len(recent) + exposed,
        "catalog_total": len(ordered),
        "complete": exposed >= len(historical),
    }

    save_json(STATE_FILE, state)

    selected = recent + historical[:exposed]

    seen = set()
    result = []

    for x in selected:
        k = item_key(x)
        if k not in seen:
            seen.add(k)
            result.append(x)

    log(
        "Backfill acumulativo: "
        f"{len(recent)} recientes + "
        f"{exposed}/{len(historical)} históricos "
        f"= {len(result)} entradas expuestas."
    )

    if state["complete"]:
        log(
            "Backfill COMPLETADO: "
            "feed.xml contiene todo el catálogo disponible."
        )

    return result


def build_feed(catalog: list[dict]) -> None:
    items = choose_feed_items(catalog)
    build_time = datetime.now(timezone.utc)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<rss version="2.0" '
            'xmlns:atom="http://www.w3.org/2005/Atom" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        ),
        "<channel>",
        "<title>SER Historia — Archivo completo</title>",
        f"<link>{xml_escape(INDEX_URL)}</link>",
        (
            "<description>"
            "Archivo histórico de SER Historia a partir de "
            "la web oficial de Nacho Ares."
            "</description>"
        ),
        "<language>es-es</language>",
        f"<lastBuildDate>{format_datetime(build_time)}</lastBuildDate>",
        (
            '<atom:link '
            'href="https://raw.githubusercontent.com/'
            'luisdrico-prog/ser-historia-rss/main/feed.xml" '
            'rel="self" type="application/rss+xml" />'
        ),
    ]

    for it in items:
        pub = parse_dt(it.get("published"))
        number = it.get("number")
        title = it.get("title") or f"SER Historia {number or ''}"
        page_url = it.get("url") or INDEX_URL
        ivoox = (
            it.get("ivoox_url")
            or it.get("ivoox_player")
            or page_url
        )

        body = []

        if it.get("image"):
            body.append(
                f'<p><img src="{xml_escape(it["image"])}" '
                f'alt="{xml_escape(title)}" /></p>'
            )

        if it.get("description"):
            body.append(
                f"<p>{html.escape(it['description'])}</p>"
            )

        body.append(
            f'<p><a href="{xml_escape(ivoox)}">'
            "🎧 Escuchar programa</a></p>"
        )

        body.append(
            f'<p><a href="{xml_escape(page_url)}">'
            "📚 Ver ficha en NachoAres.com</a></p>"
        )

        guid = it.get("guid") or page_url

        parts.extend([
            "<item>",
            f"<title>{cdata(title)}</title>",
            f"<link>{xml_escape(page_url)}</link>",
            (
                '<guid isPermaLink="false">'
                f"{xml_escape(guid)}</guid>"
            ),
            f"<pubDate>{format_datetime(pub)}</pubDate>",
            (
                "<description>"
                f"{cdata(it.get('description') or '')}"
                "</description>"
            ),
            (
                "<content:encoded>"
                f"{cdata(''.join(body))}"
                "</content:encoded>"
            ),
            "</item>",
        ])

    parts.extend([
        "</channel>",
        "</rss>",
    ])

    FEED_FILE.write_text(
        "\n".join(parts) + "\n",
        encoding="utf-8",
    )

    log(
        f"RSS generado: {FEED_FILE} "
        f"con {len(items)} entradas expuestas."
    )


def main():
    catalog = crawl_catalog()
    save_json(CATALOG_FILE, catalog)
    build_feed(catalog)

    nums = [
        x.get("number")
        for x in catalog
        if isinstance(x.get("number"), int)
    ]

    if nums:
        log(
            f"Rango detectado: "
            f"SER Historia {min(nums)} → {max(nums)}"
        )

    log("Terminado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
