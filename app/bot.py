"""Telegram-bot: het hele dashboard vanuit je chat.

De bot haalt zelf berichten op bij Telegram ("long polling"), dus er is
geen webadres of webhook nodig. Hij reageert alleen op jouw chat-id.

Commando's (of typ gewoon een zin, bijv. "ik heb 10 ASML gekocht voor 612"):
  /overzicht                      portefeuille met adviezen
  /advies ASML                    laatste advies met uitleg
  /koop ASML 10 612               aankoop toevoegen (koers weglaten = huidige koers)
  /verkoop ASML 5 650             verkoop toevoegen
  /bijkoop ASML 5 590             wat wordt mijn gemiddelde als ik 5 koop op 590?
  /doel ASML 600                  hoeveel moet ik bijkopen voor gemiddelde 600?
  /analyse [ASML]                 agents nu laten draaien
  /transacties ASML               alle aankopen/verkopen
  /notitie ASML lange termijn     jouw reden (de agents lezen mee)
  /verwijder ASML                 aandeel verwijderen
"""
import logging
import math
import os
import threading
import time
import uuid

import httpx

from . import analysis, market, telegram as tg
from .db import SessionLocal, Position, Trade

log = logging.getLogger("bot")
_stop = threading.Event()
_pending_actions: dict = {}   # bevestigingen die op een "Ja"-knop wachten

HELP = """<b>📈 Aandelen-bot</b>

Typ gewoon wat je wilt, bijvoorbeeld:
• <i>ik heb 10 ASML gekocht voor 612</i>
• <i>wat moet ik met Shell?</i>
• <i>wat wordt mijn gemiddelde als ik 5 ASML bijkoop op 590?</i>

Of gebruik commando's:
/overzicht – portefeuille met adviezen
/advies ASML – advies met uitleg
/koop ASML 10 612 – aankoop toevoegen
/verkoop ASML 5 650 – verkoop toevoegen
/bijkoop ASML 5 590 – nieuw gemiddelde berekenen
/doel ASML 600 – hoeveel bijkopen voor dat gemiddelde
/analyse – agents nu laten draaien
/transacties ASML – al je aankopen
/notitie ASML tekst – jouw reden voor het aandeel
/verwijder ASML – aandeel verwijderen

Amsterdamse aandelen hebben .AS (ASML.AS). Je mag ook gewoon de naam typen."""

MENU = [[("📋 Overzicht", "overview"), ("↻ Analyseer alles", "runall")]]


# ================================================================== start/stop
def start():
    if not tg.TOKEN:
        log.info("Telegram-bot uit (geen TELEGRAM_BOT_TOKEN)")
        return
    tg.call("deleteWebhook")
    tg.call("setMyCommands", commands=[
        {"command": c, "description": d} for c, d in [
            ("overzicht", "Portefeuille met adviezen"), ("advies", "Advies met uitleg, bijv. /advies ASML"),
            ("koop", "Aankoop: /koop ASML 10 612"), ("verkoop", "Verkoop: /verkoop ASML 5 650"),
            ("bijkoop", "Nieuw gemiddelde: /bijkoop ASML 5 590"), ("doel", "Doel-gemiddelde: /doel ASML 600"),
            ("analyse", "Agents nu laten draaien"), ("transacties", "Aankopen van een aandeel"),
            ("notitie", "Jouw reden: /notitie ASML lange termijn"), ("verwijder", "Aandeel verwijderen"),
            ("help", "Uitleg")]])
    threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot").start()
    log.info("Telegram-bot gestart")


def stop():
    _stop.set()


def _poll_loop():
    offset = None
    while not _stop.is_set():
        try:
            r = httpx.post(f"{tg.API}/getUpdates", timeout=60, json={
                "timeout": 50, "offset": offset, "allowed_updates": ["message", "callback_query"]})
            data = r.json()
            if not data.get("ok"):
                # 409 = er draait tijdelijk nog een oude versie (tijdens een deploy)
                time.sleep(5)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception:
                    log.exception("bericht verwerken mislukt")
        except Exception as ex:
            log.warning("polling fout: %s", tg.safe(ex))
            time.sleep(5)


# ================================================================== hulpjes
def _num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("€", "").replace("$", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def find_position(s, text: str):
    """Zoek een positie op ticker, ticker zonder .AS, of (deel van) de naam."""
    t = text.strip().upper()
    for p in s.query(Position).all():
        base = p.ticker.split(".")[0]
        if t in (p.ticker, base) or (len(t) >= 3 and t.lower() in (p.name or "").lower()):
            return p
    return None


def resolve_ticker(text: str):
    """Geeft (ticker, naam, bestaat_al) — voor een aandeel dat je nog niet hebt via Yahoo zoeken."""
    with SessionLocal() as s:
        p = find_position(s, text)
        if p:
            return p.ticker, p.name, True
    res = market.search_tickers(text)
    if res:
        # Voorkeur voor Amsterdam als die er is en er niet expliciet een beurs is genoemd
        if "." not in text:
            ams = [r for r in res if r["ticker"].upper().endswith(".AS")]
            if ams and ams[0]["ticker"].split(".")[0].upper() == res[0]["ticker"].split(".")[0].upper():
                return ams[0]["ticker"], ams[0]["name"], False
        return res[0]["ticker"], res[0]["name"], False
    q = market.get_quote(text.upper())
    if q["price"]:
        return text.upper(), q["name"], False
    return None, None, False


def _ask_confirm(chat, text, action: dict):
    key = uuid.uuid4().hex[:10]
    _pending_actions[key] = action
    tg.send(text, chat, buttons=[[("✅ Ja", f"ok:{key}"), ("❌ Nee", f"no:{key}")]])


# ================================================================== berichten
def handle_update(upd):
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat = str(cq["message"]["chat"]["id"])
        tg.call("answerCallbackQuery", callback_query_id=cq["id"])
        if chat != tg.CHAT_ID:
            return
        return handle_button(chat, cq.get("data", ""))

    msg = upd.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat or not text:
        return
    if not tg.CHAT_ID or chat != tg.CHAT_ID:
        tg.send(f"Jouw chat-id is <code>{chat}</code>.\nZet deze in Railway als <b>TELEGRAM_CHAT_ID</b>. "
                "Daarna reageert de bot alleen op jou.", chat)
        return

    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0][1:].lower().split("@")[0]
        return handle_command(chat, cmd, parts[1:], text)
    return handle_free_text(chat, text)


def handle_command(chat, cmd, args, raw=""):
    a = lambda i: args[i] if len(args) > i else None
    if cmd in ("start", "help", "menu"):
        tg.send(HELP, chat, buttons=MENU)
    elif cmd == "overzicht":
        overview(chat)
    elif cmd == "advies":
        if not a(0):
            return tg.send("Welk aandeel? Bijv. /advies ASML", chat)
        show_advice(chat, a(0))
    elif cmd in ("koop", "verkoop"):
        if not a(0) or not _num(a(1)):
            return tg.send(f"Gebruik: /{cmd} ASML 10 612  (koers weglaten = huidige koers)", chat)
        trade(chat, "buy" if cmd == "koop" else "sell", a(0), _num(a(1)), _num(a(2)), confirm=False)
    elif cmd == "bijkoop":
        if not a(0) or not _num(a(1)):
            return tg.send("Gebruik: /bijkoop ASML 5 590  (koers weglaten = huidige koers)", chat)
        calc_buy(chat, a(0), _num(a(1)), _num(a(2)))
    elif cmd == "doel":
        if not a(0) or not _num(a(1)):
            return tg.send("Gebruik: /doel ASML 600  (optioneel daarna de aankoopkoers)", chat)
        calc_target(chat, a(0), _num(a(1)), _num(a(2)))
    elif cmd == "analyse":
        run_analysis(chat, a(0))
    elif cmd == "transacties":
        if not a(0):
            return tg.send("Gebruik: /transacties ASML", chat)
        trades(chat, a(0))
    elif cmd == "notitie":
        if not a(0) or len(args) < 2:
            return tg.send("Gebruik: /notitie ASML lange termijn, groei in chips", chat)
        note(chat, a(0), " ".join(args[1:]))
    elif cmd == "verwijder":
        if not a(0):
            return tg.send("Gebruik: /verwijder ASML", chat)
        with SessionLocal() as s:
            p = find_position(s, a(0))
            if not p:
                return tg.send("Dat aandeel heb ik niet gevonden.", chat)
            _ask_confirm(chat, f"<b>{tg.e(p.name)}</b> ({p.ticker}) met alle aankopen en adviezen verwijderen?",
                         {"type": "delete", "pid": p.id})
    else:
        tg.send("Dat commando ken ik niet. Typ /help.", chat)


def handle_button(chat, data):
    kind, _, arg = data.partition(":")
    if kind == "overview":
        return overview(chat)
    if kind == "runall":
        return run_analysis(chat, None)
    if kind == "adv":
        return show_advice(chat, arg)
    if kind == "run":
        return run_analysis(chat, arg)
    if kind == "calc":
        return calc_suggestion(chat, arg)
    if kind == "pos":
        return position(chat, arg)
    if kind in ("news", "anal", "src"):
        with SessionLocal() as s:
            p = find_position(s, arg)
            if not p or not p.advices:
                return tg.send("Nog geen advies voor dit aandeel.", chat)
            adv = p.advices[0].to_dict()
        fmt = {"news": tg.format_news, "anal": tg.format_analyst, "src": tg.format_sources}[kind]
        return tg.send(fmt(p.ticker, adv), chat)
    if kind == "no":
        _pending_actions.pop(arg, None)
        return tg.send("Oké, niets gedaan.", chat)
    if kind == "ok":
        act = _pending_actions.pop(arg, None)
        if not act:
            return tg.send("Deze vraag is verlopen. Probeer het opnieuw.", chat)
        if act["type"] == "trade":
            return do_trade(chat, act)
        if act["type"] == "delete":
            with SessionLocal() as s:
                p = s.get(Position, act["pid"])
                if p:
                    s.delete(p)
                    s.commit()
            return tg.send("🗑 Verwijderd.", chat, buttons=MENU)


# ================================================================== acties
def overview(chat):
    with SessionLocal() as s:
        pf = analysis.portfolio(s, light=True)
    if not pf["positions"]:
        return tg.send("Je hebt nog geen aandelen.\nTyp bijvoorbeeld: <i>ik heb 10 ASML gekocht voor 612</i>", chat)
    lines = [f"💼 <b>Portefeuille {tg.money(pf['total_eur'])}</b>",
             f"{'🟢' if pf['pl_eur'] >= 0 else '🔴'} {tg.pct(pf['pl_pct'])} t.o.v. inleg ({tg.money(pf['pl_eur'])})", ""]
    btns, row = [], []
    for v in pf["positions"]:
        a = v["advice"]
        act = f"{tg.EMOJI.get(a['action'], '')} {a['action']}" if a else "⚪ nog geen advies"
        lines.append(f"<b>{tg.e(v['ticker'])}</b> {tg.money(v['price'], v['currency'])} · {tg.pct(v['pl_pct'])}\n"
                     f"   {act}" + (f" – <i>{tg.e(a['headline'])}</i>" if a else ""))
        row.append((v["ticker"].split(".")[0], f"adv:{v['ticker']}"))
        if len(row) == 3:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    if analysis.status["running"]:
        lines.append(f"\n⏳ Agents zijn bezig ({analysis.status['current'] or '…'})")
    tg.send("\n".join(lines), chat, buttons=btns + [[("↻ Analyseer alles", "runall")]])


def show_advice(chat, text):
    with SessionLocal() as s:
        p = find_position(s, text)
        if not p:
            return tg.send(f"‘{tg.e(text)}’ zit niet in je portefeuille. Toevoegen? Typ bijv. <i>/koop {tg.e(text.upper())} 10</i>", chat)
        if not p.advices:
            return tg.send(f"Nog geen advies voor {p.ticker}.", chat, buttons=[[("↻ Analyseer nu", f"run:{p.ticker}")]])
        adv, ticker, name, cur = p.advices[0].to_dict(), p.ticker, p.name, p.currency
    q = market.get_quote(ticker)
    txt = tg.format_advice(ticker, name, adv, None, currency=cur)
    txt += f"\n\n<i>Advies van {adv['created_at'][:16].replace('T', ' ')} UTC · koers nu {tg.money(q['price'], cur)}</i>"
    tg.send(txt, chat, buttons=tg.advice_buttons(ticker))


def position(chat, text):
    with SessionLocal() as s:
        p = find_position(s, text)
        if not p:
            return tg.send("Niet gevonden.", chat)
        pf = analysis.portfolio(s, light=True)
    v = next(x for x in pf["positions"] if x["id"] == p.id)
    cur = v["currency"]
    txt = (f"📈 <b>{tg.e(v['name'])}</b> · {v['ticker']}\n\n"
           f"Aantal: <b>{v['quantity']:g}</b>\n"
           f"Gem. aankoopkoers: <b>{tg.money(v['avg_price'], cur)}</b>\n"
           f"Koers nu: <b>{tg.money(v['price'], cur)}</b> ({tg.pct(v['day_change_pct'])} vandaag)\n"
           f"Waarde: {tg.money(v['value'], cur)}\n"
           f"Winst/verlies: <b>{tg.money(v['pl'], cur)}</b> ({tg.pct(v['pl_pct'])})\n"
           f"Deel van portefeuille: {v['weight_pct']}%")
    if v["notes"]:
        txt += f"\n\n📝 <i>{tg.e(v['notes'])}</i>"
    tg.send(txt, chat, buttons=[[("💡 Advies", f"adv:{v['ticker']}"), ("🧮 Bijkopen?", f"calc:{v['ticker']}")]])


def trade(chat, side, ticker_text, qty, price, confirm=True):
    tg.typing(chat)
    ticker, name, exists = resolve_ticker(ticker_text)
    if not ticker:
        return tg.send(f"Ik kan ‘{tg.e(ticker_text)}’ niet vinden. Probeer de ticker, bijv. ASML.AS of AAPL.", chat)
    if side == "sell" and not exists:
        return tg.send(f"Je hebt geen {tg.e(ticker)} in je portefeuille.", chat)
    q = market.get_quote(ticker)
    price = price or q["price"]
    act = {"type": "trade", "side": side, "ticker": ticker, "name": name, "qty": qty, "price": price}
    word = "Gekocht" if side == "buy" else "Verkocht"
    summary = (f"{word}: <b>{qty:g} × {tg.e(name)}</b> ({ticker})\n"
               f"Koers: <b>{tg.money(price, q['currency'])}</b> · totaal {tg.money(qty * price, q['currency'])}")
    if not exists:
        return _ask_confirm(chat, f"Nieuw aandeel:\n{summary}\n\nKlopt dit?", act)
    if confirm:
        return _ask_confirm(chat, f"{summary}\n\nOpslaan?", act)
    do_trade(chat, act)


def do_trade(chat, act):
    try:
        pid, new = analysis.add_trade(act["ticker"], act["side"], act["qty"], act["price"], name=act["name"])
    except analysis.TickerNotFound:
        return tg.send("Dat aandeel kon ik niet vinden.", chat)
    with SessionLocal() as s:
        p = s.get(Position, pid)
        st = p.stats()
        cur = p.currency
    txt = (f"✅ Opgeslagen. Je hebt nu <b>{st['quantity']:g}</b> {act['ticker']}, "
           f"gemiddelde koers <b>{tg.money(st['avg_price'], cur)}</b>.")
    if new and os.getenv("ANTHROPIC_API_KEY"):
        analysis.run_in_background([pid], notify=True)
        txt += "\n\n⏳ De agents maken nu een eerste advies (±30 sec)…"
    tg.send(txt, chat, buttons=None if new else [[("💡 Advies", f"adv:{act['ticker']}"), ("📋 Overzicht", "overview")]])


def run_analysis(chat, text):
    if not os.getenv("ANTHROPIC_API_KEY"):
        return tg.send("ANTHROPIC_API_KEY ontbreekt in Railway, de agents kunnen nog niet werken.", chat)
    ids = None
    if text:
        with SessionLocal() as s:
            p = find_position(s, text)
            if not p:
                return tg.send("Dat aandeel zit niet in je portefeuille.", chat)
            ids = [p.id]
    analysis.run_in_background(ids, notify=True)
    tg.send("⏳ Agents gestart. Je krijgt het advies zo meteen" +
            (" (±30 sec)." if ids else " per aandeel binnen."), chat)


def _pos_numbers(text):
    with SessionLocal() as s:
        p = find_position(s, text)
        if not p:
            return None
        st = p.stats()
        adv = p.advices[0].to_dict() if p.advices else None
        return p.ticker, p.currency, st["quantity"], st["avg_price"], adv


def calc_buy(chat, text, qty, price):
    r = _pos_numbers(text)
    if not r:
        return tg.send("Dat aandeel zit niet in je portefeuille.", chat)
    ticker, cur, q, avg, _ = r
    price = price or market.get_quote(ticker)["price"]
    nq = q + qty
    navg = (q * avg + qty * price) / nq
    diff = navg - avg
    tg.send(f"🧮 <b>{ticker}</b>: {qty:g} stuks bijkopen op {tg.money(price, cur)}\n\n"
            f"Gemiddelde nu: {tg.money(avg, cur)}\n"
            f"Nieuw gemiddelde: <b>{tg.money(navg, cur)}</b> ({'▼' if diff < 0 else '▲'} {tg.money(abs(diff), cur)}, {tg.pct(diff / avg * 100 if avg else 0)})\n"
            f"Kost: {tg.money(qty * price, cur)} · daarna {nq:g} stuks"
            + ("\n\n⚠️ Deze koers ligt boven je gemiddelde: bijkopen verhoogt je gemiddelde." if diff > 0 else ""), chat)


def calc_target(chat, text, target, price):
    r = _pos_numbers(text)
    if not r:
        return tg.send("Dat aandeel zit niet in je portefeuille.", chat)
    ticker, cur, q, avg, _ = r
    price = price or market.get_quote(ticker)["price"]
    if not (price < target < avg):
        return tg.send(f"Dat kan niet: het doel moet tussen de aankoopkoers ({tg.money(price, cur)}) "
                       f"en je huidige gemiddelde ({tg.money(avg, cur)}) liggen.", chat)
    need = math.ceil(q * (avg - target) / (target - price))
    tg.send(f"🎯 <b>{ticker}</b>: om je gemiddelde van {tg.money(avg, cur)} naar <b>{tg.money(target, cur)}</b> te brengen "
            f"koop je <b>{need} stuks</b> op {tg.money(price, cur)}.\nKost: {tg.money(need * price, cur)}", chat)


def calc_suggestion(chat, text):
    r = _pos_numbers(text)
    if not r:
        return tg.send("Niet gevonden.", chat)
    ticker, cur, q, avg, adv = r
    now = market.get_quote(ticker)["price"]
    zone = adv.get("buy_zone") if adv else None
    qty = max(1, round(q / 4))
    lines = [f"🧮 <b>Bijkopen {ticker}?</b>  Gemiddelde nu {tg.money(avg, cur)} · koers {tg.money(now, cur)}", ""]
    for label, p in [("op de huidige koers", now), ("in de bijkoopzone", zone)]:
        if p:
            navg = (q * avg + qty * p) / (q + qty)
            lines.append(f"{qty} stuks {label} ({tg.money(p, cur)}) → gemiddelde <b>{tg.money(navg, cur)}</b> ({tg.pct((navg / avg - 1) * 100)})")
    if adv:
        lines.append(f"\nAdvies agents: <b>{adv['action']}</b> – <i>{tg.e(adv['headline'])}</i>")
    lines.append(f"\nZelf rekenen: <code>/bijkoop {ticker} 5 {(zone or now or 0):.2f}</code>")
    tg.send("\n".join(lines), chat)


def trades(chat, text):
    with SessionLocal() as s:
        p = find_position(s, text)
        if not p:
            return tg.send("Niet gevonden.", chat)
        rows = [f"{t.trade_date:%d-%m-%Y}  {'+' if t.side == 'buy' else '−'}{t.quantity:g} × {tg.money(t.price, p.currency)}"
                for t in p.trades]
        st = p.stats()
        tg.send(f"🧾 <b>{p.ticker}</b>\n<code>" + "\n".join(rows or ["geen"]) + "</code>\n\n"
                f"Gemiddelde: <b>{tg.money(st['avg_price'], p.currency)}</b> over {st['quantity']:g} stuks"
                "\n\nFoutje? Verwijderen kan in het dashboard of met /verwijder.", chat)


def note(chat, text, content):
    with SessionLocal() as s:
        p = find_position(s, text)
        if not p:
            return tg.send("Niet gevonden.", chat)
        p.notes = content
        s.commit()
    tg.send("📝 Opgeslagen. De agents nemen dit mee bij het volgende advies.", chat)


# ================================================================== gewone zinnen
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intentie": {"type": "string", "enum": [
            "koop", "verkoop", "advies", "overzicht", "analyse", "bijkoop_berekenen",
            "doelgemiddelde", "positie", "transacties", "notitie", "help", "onbekend"]},
        "aandeel": {"type": ["string", "null"], "description": "ticker of bedrijfsnaam zoals genoemd"},
        "aantal": {"type": ["number", "null"]},
        "koers": {"type": ["number", "null"], "description": "prijs per aandeel"},
        "doel": {"type": ["number", "null"], "description": "gewenst gemiddelde (alleen bij doelgemiddelde)"},
        "tekst": {"type": ["string", "null"], "description": "notitietekst"},
    },
    "required": ["intentie"],
}


def handle_free_text(chat, text):
    if not os.getenv("ANTHROPIC_API_KEY"):
        return tg.send("Gebruik een commando, bijv. /koop ASML 10 612. Typ /help voor alles.", chat)
    tg.typing(chat)
    from . import agents
    with SessionLocal() as s:
        mine = ", ".join(f"{p.ticker} ({p.name})" for p in s.query(Position).all()) or "geen"
    try:
        r = agents._call(
            agents.MODEL_NEWS,
            "Je zet een Nederlands chatbericht van een belegger om naar een actie voor zijn aandelen-bot. "
            "Bedragen: '612,50' = 612.5. 'gekocht/ingestapt/bijgekocht' = koop. 'wat moet ik met X / advies / "
            "kopen of verkopen?' = advies. 'wat wordt mijn gemiddelde als ik ... bijkoop' = bijkoop_berekenen. "
            "'hoeveel moet ik bijkopen om op gemiddeld X te komen' = doelgemiddelde. "
            f"Aandelen die hij al heeft: {mine}.",
            text, "actie", INTENT_SCHEMA, 300)
    except Exception as ex:
        log.warning("intentie mislukt: %s", ex)
        return tg.send("Dat begreep ik niet helemaal. Typ /help voor de commando's.", chat)

    i, stock = r.get("intentie"), r.get("aandeel")
    qty, price = _num(r.get("aantal")), _num(r.get("koers"))
    needs_stock = {"koop", "verkoop", "advies", "bijkoop_berekenen", "doelgemiddelde", "positie", "transacties", "notitie"}
    if i in needs_stock and not stock:
        return tg.send("Over welk aandeel gaat het?", chat)
    if i in ("koop", "verkoop"):
        if not qty:
            return tg.send("Hoeveel stuks? Bijv. <i>10 ASML gekocht voor 612</i>", chat)
        return trade(chat, "buy" if i == "koop" else "sell", stock, qty, price, confirm=True)
    if i == "advies":
        return show_advice(chat, stock)
    if i == "overzicht":
        return overview(chat)
    if i == "analyse":
        return run_analysis(chat, stock)
    if i == "bijkoop_berekenen":
        if not qty:
            return calc_suggestion(chat, stock)
        return calc_buy(chat, stock, qty, price)
    if i == "doelgemiddelde":
        if not _num(r.get("doel")):
            return tg.send("Welk gemiddelde wil je bereiken?", chat)
        return calc_target(chat, stock, _num(r.get("doel")), price)
    if i == "positie":
        return position(chat, stock)
    if i == "transacties":
        return trades(chat, stock)
    if i == "notitie" and r.get("tekst"):
        return note(chat, stock, r["tekst"])
    tg.send(HELP, chat, buttons=MENU)
