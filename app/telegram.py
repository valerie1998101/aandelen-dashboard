"""Telegram: berichten versturen en opmaken.

Optioneel. Werkt zodra TELEGRAM_BOT_TOKEN (en TELEGRAM_CHAT_ID) zijn ingesteld.
De bot zelf (commando's en knoppen) staat in bot.py.
"""
import html
import logging
import os

import httpx

log = logging.getLogger("telegram")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API = f"https://api.telegram.org/bot{TOKEN}"

EMOJI = {"KOPEN": "🟢", "BIJKOPEN": "🟢", "HOUDEN": "🟡", "AFBOUWEN": "🟠", "VERKOPEN": "🔴"}
e = html.escape


def safe(ex) -> str:
    """Foutmelding zonder token (httpx-fouten bevatten soms de volledige URL)."""
    return str(ex).replace(TOKEN, "***") if TOKEN else str(ex)


def enabled():
    return bool(TOKEN and CHAT_ID)


def call(method: str, timeout=15, **payload):
    if not TOKEN:
        return None
    try:
        r = httpx.post(f"{API}/{method}", json=payload, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram %s: %s", method, data.get("description"))
        return data
    except Exception as ex:
        log.warning("telegram %s mislukt: %s", method, safe(ex))
        return None


def _markup(buttons):
    """buttons = [[("tekst", "callback_data"), ...], ...]"""
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row] for row in buttons]}


def send(text: str, chat_id: str | None = None, buttons=None):
    payload = {"chat_id": chat_id or CHAT_ID, "text": text[:4000], "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = _markup(buttons)
    return call("sendMessage", **payload)


def typing(chat_id=None):
    call("sendChatAction", chat_id=chat_id or CHAT_ID, action="typing")


def money(v, cur="EUR"):
    if v is None:
        return "–"
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(cur, "")
    if cur in ("GBp", "GBX"):
        return f"{v:,.1f}p".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if v < 0 else ""
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}{sym}{s}" if sym else f"{sign}{s} {cur}"


def pct(v):
    return "–" if v is None else f"{v:+.1f}%".replace(".", ",")


def advice_buttons(ticker):
    return [
        [("📰 Nieuws", f"news:{ticker}"), ("📊 Analist", f"anal:{ticker}"), ("🔗 Bronnen", f"src:{ticker}")],
        [("🧮 Bijkopen?", f"calc:{ticker}"), ("📈 Positie", f"pos:{ticker}"), ("↻ Opnieuw", f"run:{ticker}")],
    ]


def format_advice(ticker, name, adv: dict, prev_action: str | None, app_url: str = "", currency="EUR"):
    changed = f"  (was {prev_action})" if prev_action and prev_action != adv["action"] else ""
    lines = [
        f"{EMOJI.get(adv['action'], '⚪')} <b>{e(name)}</b> · {e(ticker)}",
        f"<b>{adv['action']}</b>{changed} · zekerheid {adv['confidence']}%",
        f"<i>{e(adv['headline'] or '')}</i>",
        "",
        "<b>Waarom</b>",
    ]
    lines += [f"→ {e(r)}" for r in adv["reasons"]]
    if adv.get("buy_zone") or adv.get("stop_level"):
        lines.append("")
        if adv.get("buy_zone"):
            lines.append(f"🟢 Bijkopen interessant onder <b>{money(adv['buy_zone'], currency)}</b>")
        if adv.get("stop_level"):
            lines.append(f"🔴 Verhaal breekt onder <b>{money(adv['stop_level'], currency)}</b>")
    if adv.get("change_triggers"):
        lines += ["", "<b>Wat verandert het advies</b>"] + [f"• {e(x)}" for x in adv["change_triggers"]]
    if app_url:
        lines.append(f'\n<a href="{e(app_url)}">Open dashboard</a>')
    return "\n".join(lines)


def format_news(ticker, adv):
    n = adv.get("news") or {}
    imp = {"positief": "🟢", "negatief": "🔴", "neutraal": "⚪"}
    lines = [f"📰 <b>Nieuws-agent · {e(ticker)}</b>  (sentiment {n.get('sentiment', 0):+d})", "",
             e(n.get("samenvatting", ""))]
    if n.get("these_in_gevaar"):
        lines.append("\n⚠️ <b>Het nieuws raakt de kern van het bedrijf.</b>")
    ev = n.get("belangrijkste_gebeurtenissen") or []
    if ev:
        lines.append("")
        lines += [f"{imp.get(g.get('impact'), '⚪')} {e(g.get('gebeurtenis', ''))}" for g in ev]
    risks = (adv.get("risk") or {}).get("risicos") or []
    if risks:
        lines += ["", "<b>Risico's</b>"] + [f"• {e(r)}" for r in risks]
    return "\n".join(lines)


def format_analyst(ticker, adv):
    a = adv.get("analyst") or {}
    return "\n".join([
        f"📊 <b>Analist-agent · {e(ticker)}</b>", "",
        f"Trend: <b>{e(a.get('trend', '?'))}</b> · Waardering: <b>{e(a.get('waardering', '?'))}</b>", "",
        e(a.get("uitleg_techniek", "")), "",
        e(a.get("uitleg_waardering", "")), "",
        f"<i>Voorlopige mening analist: {e(a.get('voorlopige_mening', ''))} — de risico-agent nam het eindbesluit.</i>",
    ])


def format_sources(ticker, adv):
    src = adv.get("sources") or []
    if not src:
        return "Geen bronnen bij dit advies."
    lines = [f"🔗 <b>Bronnen · {e(ticker)}</b>", ""]
    for i, s in enumerate(src[:12]):
        lines.append(f'[{i}] <a href="{e(s.get("url", ""))}">{e(s.get("titel", ""))}</a>\n   <i>{e(s.get("bron", ""))} · {e(s.get("datum", ""))}</i>')
    return "\n".join(lines)
