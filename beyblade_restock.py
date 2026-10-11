#!/usr/bin/env python3
"""
Toymana restock + new-product watcher.

Alerts when a sold-out item returns, or a brand-new product is listed.
"""
import json
import os
import pathlib
import random
import re
import sys
import time

import requests

STORE       = "https://www.toymana.com"
COLLECTIONS = [c.strip() for c in os.environ.get("COLLECTIONS", "beyblade").split(",")]
ENDPOINTS   = [(c, f"{STORE}/collections/{c}/products.json?limit=250") for c in COLLECTIONS]

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK  = os.environ.get("DISCORD_WEBHOOK", "")

INTERVAL = int(os.environ.get("INTERVAL", "300"))
INCLUDE  = os.environ.get("INCLUDE", "")
NEW_MODE = os.environ.get("NEW_MODE", "all")
NEW_ONLY = os.environ.get("NEW_ONLY") == "1"

STATE = pathlib.Path("state.json")
ONCE  = "--once" in sys.argv

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json",
}


def fetch(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.ok:
                return r.json()
            print(f"HTTP {r.status_code} {url}", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"fetch error: {exc}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return None


def snapshot():
    snap, ok = {}, False
    for coll, url in ENDPOINTS:
        data = fetch(url)
        if data is None:
            continue
        ok = True
        for product in data.get("products", []):
            title = product["title"]
            haystack = f'{title} {product.get("vendor", "")}'
            if INCLUDE and not re.search(INCLUDE, haystack, re.I):
                continue
            for variant in product.get("variants", []):
                snap[str(variant["id"])] = {
                    "product":    title,
                    "variant":    variant["title"],
                    "price":      variant.get("price", "?"),
                    "available":  bool(variant.get("available")),
                    "collection": coll,
                    "listed":     (product.get("created_at") or "")[:10],
                    "url":        f'{STORE}/products/{product["handle"]}',
                }
    return snap if ok else None


def label(item):
    if item["variant"] in ("Default Title", "", None):
        return item["product"]
    return f'{item["product"]} [{item["variant"]}]'


def chunks(text, size=3500):
    out, buf = [], ""
    for block in text.split("\n\n"):
        if len(buf) + len(block) + 2 > size:
            out.append(buf)
            buf = ""
        buf += block + "\n\n"
    if buf.strip():
        out.append(buf)
    return out


def notify(message):
    print(message, flush=True)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        for part in chunks(message):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": part},
                    timeout=15,
                )
                if not r.ok:
                    print(f"telegram {r.status_code}: {r.text[:200]}", file=sys.stderr)
            except Exception as exc:
                print(f"telegram failed: {exc}", file=sys.stderr)

    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": message[:1900]}, timeout=15)
        except Exception as exc:
            print(f"discord failed: {exc}", file=sys.stderr)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def check(previous):
    current = snapshot()
    if current is None:
        return previous

    if previous:
        alerts = []
        for vid, item in current.items():
            if vid not in previous:
                if NEW_MODE == "instock" and not item["available"]:
                    continue
                tag = "NEW - IN STOCK" if item["available"] else "NEW - SOLD OUT"
                alerts.append(f'{tag}\n{label(item)}  S${item["price"]}\n'
                              f'listed {item["listed"]}  [{item["collection"]}]\n{item["url"]}')
            elif item["available"] and not previous[vid]["available"] and not NEW_ONLY:
                alerts.append(f'RESTOCK\n{label(item)}  S${item["price"]}\n{item["url"]}')

        if alerts:
            notify(f'Toymana - {len(alerts)} update(s)\n\n' + "\n\n".join(alerts))
        else:
            print("no change", flush=True)
    else:
        live = sum(1 for v in current.values() if v["available"])
        print(f"baseline saved: {len(current)} variants, {live} in stock", flush=True)

    STATE.write_text(json.dumps(current))
    return current


def main():
    state = check(load_state())
    if ONCE:
        return
    while True:
        time.sleep(INTERVAL + random.uniform(0, 30))
        state = check(state)


if __name__ == "__main__":
    main()
