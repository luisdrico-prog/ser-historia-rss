#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SER Historia — inyector histórico para Feedly (v4).

Objetivo:
- catalog.json conserva TODO el archivo.
- feed-full.xml conserva el RSS completo con fechas originales.
- feed.xml sigue conteniendo TODO el archivo, pero en cada ciclo una tanda
  recibe temporalmente una pubDate reciente para que Feedly la detecte.
- Los GUID permanecen estables: una entrada ya indexada no debería duplicarse.
- Se realizan dos pasadas completas para reducir la posibilidad de que Feedly
  se pierda una tanda entre dos sondeos.

No descarga audio ni modifica catalog.json.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

CATALOG_FILE = Path("catalog.json")
STATE_FILE = Path("feedly_injection_state.json")
FEED_FILE = Path("feed.xml")

INDEX_URL = "https://nachoares.com/ser-historia/"

BATCH_SIZE = int(os.getenv("FEEDLY_BATCH_SIZE", "80"))
HOLD_HOURS = int(os.getenv("FEEDLY_HOLD_HOURS", "6"))
PASSES = int(os.getenv("FEEDLY_PASSES", "2"))


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def xml_escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def cdata(value: str) -> str:
    value = (value or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{value}]]>"


def item_key(item: dict) -> str:
    if item.get("number") is not None:
        return f"n:{item['number']}"
    return f"u:{item.get('url', '')}"


def original_date_text(item: dict) -> str:
    dt = parse_dt(item.get("published"))
    if dt.year <= 1970:
        return "Fecha original no disponible"
    return f"Fecha original: {dt.strftime('%d/%m/%Y')}"


def load_catalog() -> list[dict]:
    catalog = load_json(CATALOG_FILE, [])
    catalog = [x for x in catalog if isinstance(x, dict)]

    dedup = {}
    for item in catalog:
        dedup[item_key(item)] = item

    ordered = list(dedup.values())
    ordered.sort(
        key=lambda x: (
            x.get("number") if isinstance(x.get("number"), int) else -1,
            parse_dt(x.get("published")).timestamp(),
        ),
        reverse=True,
    )
    return ordered


def normalize_state(total: int, now: datetime) -> dict:
    state = load_json(STATE_FILE, {})

    pass_no = int(state.get("pass", 1))
    cursor = int(state.get("cursor", 0))
    started = parse_dt(state.get("batch_started_at"))
    complete = bool(state.get("complete", False))

    if pass_no < 1:
        pass_no = 1
    if cursor < 0:
        cursor = 0
    if started.year <= 1970:
        started = now

    if complete:
        return {
            **state,
            "pass": pass_no,
            "cursor": min(cursor, total),
            "batch_started_at": started.isoformat(),
            "complete": True,
        }

    # Cada tanda permanece 6 h antes de avanzar a la siguiente.
    if now - started >= timedelta(hours=HOLD_HOURS):
        cursor += BATCH_SIZE
        started = now

        if cursor >= total:
            pass_no += 1

            if pass_no > PASSES:
                complete = True
                cursor = total
            else:
                cursor = 0

    return {
        "mode": "feedly_historical_injection",
        "pass": pass_no,
        "passes_total": PASSES,
        "cursor": cursor,
        "batch_size": BATCH_SIZE,
        "hold_hours": HOLD_HOURS,
        "batch_started_at": started.isoformat(),
        "catalog_total": total,
        "complete": complete,
        "updated_at": now.isoformat(),
    }


def build_feed(catalog: list[dict], state: dict) -> None:
    now = datetime.now(timezone.utc)
    complete = bool(state.get("complete"))
    cursor = int(state.get("cursor", 0))

    if complete:
        batch = []
        active_keys = set()
    else:
        batch = catalog[cursor: cursor + BATCH_SIZE]
        active_keys = {item_key(x) for x in batch}

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
            "Archivo histórico completo de SER Historia. "
            "Feed preparado para indexación progresiva en Feedly."
            "</description>"
        ),
        "<language>es-es</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
        (
            '<atom:link '
            'href="https://raw.githubusercontent.com/'
            'luisdrico-prog/ser-historia-rss/main/feed.xml" '
            'rel="self" type="application/rss+xml" />'
        ),
    ]

    synthetic_index = 0

    for item in catalog:
        key = item_key(item)
        original_pub = parse_dt(item.get("published"))

        # La tanda activa se presenta temporalmente como recién publicada.
        if key in active_keys:
            pub = now - timedelta(minutes=synthetic_index)
            synthetic_index += 1
        else:
            pub = original_pub

        number = item.get("number")
        title = item.get("title") or f"SER Historia {number or ''}"
        page_url = item.get("url") or INDEX_URL

        ivoox = (
            item.get("ivoox_url")
            or item.get("ivoox_player")
            or page_url
        )

        date_note = original_date_text(item)

        body = []

        if item.get("image"):
            body.append(
                f'<p><img src="{xml_escape(item["image"])}" '
                f'alt="{xml_escape(title)}" /></p>'
            )

        body.append(
            f"<p><strong>{html.escape(date_note)}</strong></p>"
        )

        if item.get("description"):
            body.append(
                f"<p>{html.escape(item['description'])}</p>"
            )

        body.append(
            f'<p><a href="{xml_escape(ivoox)}">'
            "🎧 Escuchar programa</a></p>"
        )

        body.append(
            f'<p><a href="{xml_escape(page_url)}">'
            "📚 Ver ficha en NachoAres.com</a></p>"
        )

        guid = item.get("guid") or page_url
        desc = item.get("description") or ""

        if key in active_keys:
            desc = f"{date_note}. {desc}".strip()

        parts.extend([
            "<item>",
            f"<title>{cdata(title)}</title>",
            f"<link>{xml_escape(page_url)}</link>",
            (
                '<guid isPermaLink="false">'
                f"{xml_escape(guid)}</guid>"
            ),
            f"<pubDate>{format_datetime(pub)}</pubDate>",
            f"<description>{cdata(desc)}</description>",
            (
                "<content:encoded>"
                f"{cdata(''.join(body))}"
                "</content:encoded>"
            ),
            "</item>",
        ])

    parts.extend(["</channel>", "</rss>"])

    FEED_FILE.write_text(
        "\n".join(parts) + "\n",
        encoding="utf-8",
    )

    if complete:
        log(
            f"Feedly: 2 pasadas completadas. "
            f"RSS con {len(catalog)} entradas y fechas originales."
        )
    else:
        start = cursor + 1
        end = min(cursor + len(batch), len(catalog))

        log(
            f"Feedly: pasada {state['pass']}/"
            f"{state['passes_total']} — "
            f"tanda {start}-{end} de {len(catalog)} "
            f"({len(batch)} entradas presentadas como recientes)."
        )


def main() -> None:
    catalog = load_catalog()

    if not catalog:
        raise RuntimeError("catalog.json está vacío.")

    now = datetime.now(timezone.utc)
    state = normalize_state(len(catalog), now)

    save_json(STATE_FILE, state)
    build_feed(catalog, state)

    log(
        f"Estado Feedly: pass={state.get('pass')}, "
        f"cursor={state.get('cursor')}, "
        f"complete={state.get('complete')}."
    )


if __name__ == "__main__":
    main()
