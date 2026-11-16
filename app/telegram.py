"""Telegram: meldingen sturen en simpele commando's beantwoorden.

Optioneel. Werkt alleen als TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID zijn ingesteld.
"""
import html
import logging
import os

import httpx

log = logging.getLogger("telegram")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EMOJI = {"KOPEN": "🟢", "BIJKOPEN": "🟢", "HOUDEN": "🟡", "AFBOUWEN": "🟠", "VERKOPEN": "🔴"}


def enabled():
    return bool(TOKEN and CHAT_ID)


def send(text: str, chat_id: str | None = None):
    if not TOKEN:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", timeout=15, json={
            "chat_id": chat_id or CHAT_ID, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
    except Exception as e:
        log.warning("telegram versturen mislukt: %s", e)


def set_webhook(public_url: str, secret: str):
    if not TOKEN or not public_url:
        return
    try:
        r = httpx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook", timeout=15, json={
            "url": f"{public_url.rstrip('/')}/telegram/webhook",
            "secret_token": secret,
            "allowed_updates": ["message"],
        })
        log.info("telegram webhook: %s", r.text[:200])
    except Exception as e:
        log.warning("telegram webhook instellen mislukt: %s", e)


def format_advice(ticker, name, adv: dict, prev_action: str | None, app_url: str = ""):
    e = html.escape
    changed = f" (was {prev_action})" if prev_action and prev_action != adv["action"] else ""
    lines = [
        f"{EMOJI.get(adv['action'], '⚪')} <b>{e(name)}</b> ({e(ticker)})",
        f"<b>{adv['action']}</b>{changed} · zekerheid {adv['confidence']}%",
        f"<i>{e(adv['headline'] or '')}</i>",
        "",
        "<b>Waarom:</b>",
    ]
    lines += [f"• {e(r)}" for r in adv["reasons"]]
    if adv.get("buy_zone"):
        lines.append(f"\nBijkoopzone: onder {adv['buy_zone']:.2f}")
    if app_url:
        lines.append(f'\n<a href="{e(app_url)}">Open dashboard</a>')
    return "\n".join(lines)
