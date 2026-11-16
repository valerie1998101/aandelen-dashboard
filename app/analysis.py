"""Portefeuille-berekeningen en de analyse-pijplijn (nieuws -> analist -> risico)."""
import json
import logging
import os
import threading
from datetime import datetime

from . import agents, market, telegram
from .db import SessionLocal, Position, Advice

log = logging.getLogger("analysis")
_lock = threading.Lock()
status = {"running": False, "current": None, "last_run": None, "last_error": None}


def app_url():
    if os.getenv("PUBLIC_URL"):
        return os.getenv("PUBLIC_URL")
    dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{dom}" if dom else ""


def position_view(p: Position, with_quote=True):
    s = p.stats()
    q = market.get_quote(p.ticker) if with_quote else {"price": None, "day_change_pct": None}
    price = q.get("price")
    value = (price or 0) * s["quantity"]
    fx = market.fx_to_eur(p.currency)
    latest = p.advices[0].to_dict() if p.advices else None
    return {
        "id": p.id, "ticker": p.ticker, "name": p.name, "currency": p.currency, "notes": p.notes,
        "quantity": s["quantity"], "avg_price": s["avg_price"], "invested": s["invested"],
        "realized": s["realized"],
        "price": price, "day_change_pct": q.get("day_change_pct"),
        "value": value, "value_eur": value * fx, "invested_eur": s["invested"] * fx,
        "pl": value - s["invested"] if price else None,
        "pl_pct": ((price / s["avg_price"] - 1) * 100) if price and s["avg_price"] else None,
        "fx_eur": fx,
        "advice": latest,
    }


def portfolio(session, light=False):
    positions = session.query(Position).order_by(Position.ticker).all()
    views = [position_view(p) for p in positions]
    if light:
        for v in views:
            if v["advice"]:
                v["advice"] = {k: v["advice"][k] for k in ("action", "confidence", "headline", "created_at")}
    total = sum(v["value_eur"] for v in views)
    invested = sum(v["invested_eur"] for v in views)
    for v in views:
        v["weight_pct"] = round(v["value_eur"] / total * 100, 1) if total else 0
    return {
        "positions": views,
        "total_eur": total,
        "invested_eur": invested,
        "pl_eur": total - invested,
        "pl_pct": ((total / invested - 1) * 100) if invested else 0,
    }


def analyse_position(session, p: Position, weight_pct: float):
    view = position_view(p)
    news = market.get_news(p.ticker, p.name or p.ticker)
    tech = market.get_technicals(p.ticker)
    pos_info = {
        "aantal_stuks": view["quantity"],
        "gemiddelde_aankoopkoers": round(view["avg_price"], 4),
        "huidige_koers": view["price"],
        "winst_verlies_pct": round(view["pl_pct"], 2) if view["pl_pct"] is not None else None,
        "valuta": p.currency,
        "eigen_notitie_belegger": p.notes or "",
        "heeft_positie": view["quantity"] > 0,
    }

    news_rep = agents.news_agent(p.ticker, p.name, news)
    analysis = agents.analyst_agent(p.ticker, p.name, tech, pos_info, news_rep)
    decision = agents.risk_agent(p.ticker, p.name, tech, pos_info, weight_pct, news_rep, analysis)

    prev = p.advices[0].action if p.advices else None
    adv = Advice(
        position_id=p.id,
        created_at=datetime.utcnow(),
        action=decision["actie"],
        confidence=int(decision.get("zekerheid", 50)),
        headline=decision.get("kop", ""),
        reasons_json=json.dumps(decision.get("waarom", []), ensure_ascii=False),
        change_triggers_json=json.dumps(decision.get("wat_verandert_het_advies", []), ensure_ascii=False),
        buy_zone=decision.get("bijkoopzone") or analysis.get("bijkoopzone"),
        stop_level=decision.get("breekpunt") or analysis.get("breekpunt"),
        price_at_advice=view["price"],
        news_json=json.dumps(news_rep, ensure_ascii=False),
        analyst_json=json.dumps(analysis, ensure_ascii=False),
        risk_json=json.dumps({"risicos": decision.get("risicos", [])}, ensure_ascii=False),
        sources_json=json.dumps(news, ensure_ascii=False),
    )
    session.add(adv)
    session.commit()

    # Telegram: standaard alleen als het advies verandert (of het eerste advies is)
    only_changes = os.getenv("TELEGRAM_ALLEEN_WIJZIGINGEN", "true").lower() != "false"
    forced = p.id in _notify or None in _notify
    _notify.discard(p.id)
    if telegram.enabled() and (forced or not only_changes or prev != adv.action):
        telegram.send(telegram.format_advice(p.ticker, p.name, adv.to_dict(), prev, app_url(), p.currency),
                      buttons=telegram.advice_buttons(p.ticker))
    return adv


_pending: set = set()   # posities die nog geanalyseerd moeten worden (None = alles)
_notify: set = set()    # posities waarvan je het resultaat altijd via Telegram krijgt


def run(position_ids=None):
    """Analyseer alle (of de gekozen) posities. Draait in de achtergrond.

    Komt er tijdens een lopende analyse een nieuw verzoek binnen, dan wordt
    dat daarna alsnog uitgevoerd (er gaat niets verloren).
    """
    _pending.update(position_ids or [None])
    if not _lock.acquire(blocking=False):
        return False
    status.update(running=True, last_error=None)
    try:
        while _pending:
            todo = set(_pending)
            _pending.clear()
            ids = None if None in todo else list(todo)
            with SessionLocal() as s:
                pf = portfolio(s)
                weights = {v["id"]: v["weight_pct"] for v in pf["positions"]}
                q = s.query(Position)
                if ids:
                    q = q.filter(Position.id.in_(ids))
                for p in q.all():
                    status["current"] = p.ticker
                    try:
                        analyse_position(s, p, weights.get(p.id, 0))
                    except Exception as e:
                        log.exception("analyse %s mislukt", p.ticker)
                        status["last_error"] = f"{p.ticker}: {e}"
                        s.rollback()
        _notify.clear()
        status["last_run"] = datetime.utcnow().isoformat() + "Z"
    finally:
        status.update(running=False, current=None)
        _lock.release()
    if _pending:  # binnengekomen net na de laatste ronde
        run_in_background()
    return True


def run_in_background(position_ids=None, notify=False):
    if notify:
        _notify.update(position_ids or [None])
    already = status["running"]
    if already:
        _pending.update(position_ids or [None])
        return True
    status["running"] = True  # direct tonen in de app
    threading.Thread(target=run, args=(position_ids,), daemon=True).start()
    return True


# ------------------------------------------------------------------ transacties
class TickerNotFound(Exception):
    pass


def add_trade(ticker: str, side: str, quantity: float, price: float | None,
              fees: float = 0, trade_date=None, name: str | None = None, notes: str = ""):
    """Voeg een aankoop/verkoop toe. Maakt de positie aan als die nog niet bestaat.

    Geeft (positie_id, is_nieuw) terug. Zonder koers wordt de huidige koers gebruikt.
    """
    from datetime import date
    from .db import Trade
    ticker = ticker.strip().upper()
    with SessionLocal() as s:
        p = s.query(Position).filter_by(ticker=ticker).first()
        new = p is None
        q = None
        if new:
            q = market.get_quote(ticker)
            if q["price"] is None and not name:
                raise TickerNotFound(ticker)
            p = Position(ticker=ticker, name=name or q["name"], currency=q["currency"], notes=notes)
            s.add(p)
            s.flush()
        if quantity and quantity > 0:
            if not price:
                price = (q or market.get_quote(ticker))["price"]
            s.add(Trade(position_id=p.id, side=side, quantity=quantity, price=price,
                        fees=fees or 0, trade_date=trade_date or date.today()))
        s.commit()
        return p.id, new
