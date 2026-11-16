"""Aandelen-dashboard: web-app + API + planner.

Start lokaal:   uvicorn app.main:app --reload
Op Railway:     zie railway.json (gebeurt automatisch)
"""
import hmac
import logging
import os
import secrets
from datetime import date
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature
from pydantic import BaseModel

from . import analysis, market, telegram
from .db import SessionLocal, Position, Trade, Advice, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
TZ = os.getenv("TIJDZONE", "Europe/Amsterdam")
ANALYSE_TIJDEN = os.getenv("ANALYSE_TIJDEN", "09:30,17:45")  # werkdagen, leeg = uit
COOKIE = "sessie"
signer = URLSafeTimedSerializer(SECRET_KEY, salt="login")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Aandelen-dashboard")
scheduler = BackgroundScheduler(timezone=TZ)


# ------------------------------------------------------------------ opstarten
@app.on_event("startup")
def startup():
    init_db()
    if not APP_PASSWORD:
        log.warning("APP_PASSWORD is niet ingesteld: het dashboard is zonder wachtwoord bereikbaar!")
    for t in [x.strip() for x in ANALYSE_TIJDEN.split(",") if x.strip()]:
        h, m = t.split(":")
        scheduler.add_job(analysis.run, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=TZ),
                          id=f"analyse-{t}", replace_existing=True, misfire_grace_time=3600)
    scheduler.start()
    log.info("Automatische analyse op werkdagen om: %s (%s)", ANALYSE_TIJDEN or "uit", TZ)
    if telegram.TOKEN and analysis.app_url():
        telegram.set_webhook(analysis.app_url(), _tg_secret())


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown(wait=False)


def _tg_secret():
    return hmac.new(SECRET_KEY.encode(), b"telegram", "sha256").hexdigest()[:48]


# ------------------------------------------------------------------ inloggen
def require_login(request: Request):
    if not APP_PASSWORD:
        return True
    tok = request.cookies.get(COOKIE)
    try:
        if tok and signer.loads(tok, max_age=60 * 60 * 24 * 90) == "ok":
            return True
    except BadSignature:
        pass
    raise HTTPException(401, "Niet ingelogd")


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login, response: Response):
    if not APP_PASSWORD or hmac.compare_digest(body.password, APP_PASSWORD):
        response.set_cookie(COOKIE, signer.dumps("ok"), max_age=60 * 60 * 24 * 90,
                            httponly=True, samesite="lax", secure=bool(os.getenv("RAILWAY_ENVIRONMENT")))
        return {"ok": True}
    raise HTTPException(401, "Wachtwoord klopt niet")


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(_=Depends(require_login)):
    return {"ok": True, "telegram": telegram.enabled(), "schedule": ANALYSE_TIJDEN, "tz": TZ,
            "ai": bool(os.getenv("ANTHROPIC_API_KEY"))}


# ------------------------------------------------------------------ portefeuille
@app.get("/api/portfolio")
def get_portfolio(_=Depends(require_login)):
    with SessionLocal() as s:
        pf = analysis.portfolio(s, light=True)
    pf["status"] = analysis.status
    return pf


@app.get("/api/search")
def search(q: str, _=Depends(require_login)):
    return market.search_tickers(q) if len(q) >= 2 else []


@app.get("/api/quote/{ticker}")
def quote(ticker: str, _=Depends(require_login)):
    return market.get_quote(ticker.upper())


class NewTrade(BaseModel):
    side: str = "buy"
    quantity: float
    price: float
    fees: float = 0
    trade_date: date | None = None


class NewPosition(NewTrade):
    ticker: str
    name: str | None = None
    notes: str = ""


@app.post("/api/positions")
def add_position(body: NewPosition, _=Depends(require_login)):
    ticker = body.ticker.strip().upper()
    with SessionLocal() as s:
        p = s.query(Position).filter_by(ticker=ticker).first()
        if not p:
            q = market.get_quote(ticker)
            if q["price"] is None and not body.name:
                raise HTTPException(400, f"Ticker {ticker} niet gevonden. Tip: Nederlandse aandelen eindigen op .AS (bijv. ASML.AS)")
            p = Position(ticker=ticker, name=body.name or q["name"], currency=q["currency"], notes=body.notes)
            s.add(p)
            s.flush()
        if body.quantity > 0:
            s.add(Trade(position_id=p.id, side=body.side, quantity=body.quantity, price=body.price,
                        fees=body.fees, trade_date=body.trade_date or date.today()))
        s.commit()
        pid = p.id
    if os.getenv("ANTHROPIC_API_KEY"):
        analysis.run_in_background([pid])  # meteen een eerste advies laten maken
    return {"id": pid}


@app.get("/api/positions/{pid}")
def get_position(pid: int, _=Depends(require_login)):
    with SessionLocal() as s:
        p = s.get(Position, pid)
        if not p:
            raise HTTPException(404)
        pf = analysis.portfolio(s)
        view = next(v for v in pf["positions"] if v["id"] == pid)
        view["trades"] = [{"id": t.id, "side": t.side, "quantity": t.quantity, "price": t.price,
                           "fees": t.fees, "date": t.trade_date.isoformat()} for t in p.trades]
        view["advices"] = [a.to_dict() for a in p.advices[:15]]
    view["history"] = market.get_history(view["ticker"])
    view["status"] = analysis.status
    return view


class PositionUpdate(BaseModel):
    notes: str | None = None
    name: str | None = None


@app.patch("/api/positions/{pid}")
def update_position(pid: int, body: PositionUpdate, _=Depends(require_login)):
    with SessionLocal() as s:
        p = s.get(Position, pid)
        if not p:
            raise HTTPException(404)
        if body.notes is not None:
            p.notes = body.notes
        if body.name:
            p.name = body.name
        s.commit()
    return {"ok": True}


@app.delete("/api/positions/{pid}")
def delete_position(pid: int, _=Depends(require_login)):
    with SessionLocal() as s:
        p = s.get(Position, pid)
        if p:
            s.delete(p)
            s.commit()
    return {"ok": True}


@app.post("/api/positions/{pid}/trades")
def add_trade(pid: int, body: NewTrade, _=Depends(require_login)):
    with SessionLocal() as s:
        if not s.get(Position, pid):
            raise HTTPException(404)
        s.add(Trade(position_id=pid, side=body.side, quantity=body.quantity, price=body.price,
                    fees=body.fees, trade_date=body.trade_date or date.today()))
        s.commit()
    return {"ok": True}


@app.delete("/api/trades/{tid}")
def delete_trade(tid: int, _=Depends(require_login)):
    with SessionLocal() as s:
        t = s.get(Trade, tid)
        if t:
            s.delete(t)
            s.commit()
    return {"ok": True}


class AnalyseRequest(BaseModel):
    position_id: int | None = None


@app.post("/api/analyse")
def analyse(body: AnalyseRequest, _=Depends(require_login)):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "ANTHROPIC_API_KEY ontbreekt in de instellingen")
    started = analysis.run_in_background([body.position_id] if body.position_id else None)
    return {"started": started, "status": analysis.status}


@app.get("/api/status")
def get_status(_=Depends(require_login)):
    return analysis.status


# ------------------------------------------------------------------ telegram
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != _tg_secret():
        raise HTTPException(403)
    upd = await request.json()
    msg = upd.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}
    if not telegram.CHAT_ID or chat_id != telegram.CHAT_ID:
        # Alleen jouw eigen chat krijgt antwoord. Zo vind je ook je chat-id.
        telegram.send(f"Jouw chat-id is <code>{chat_id}</code>. Zet deze als TELEGRAM_CHAT_ID in Railway.", chat_id)
        return {"ok": True}

    cmd = text.split()[0].lower().split("@")[0]
    if cmd in ("/start", "/help"):
        telegram.send("Commando's:\n/overzicht – je posities met advies\n/analyse – nu alles laten analyseren\n"
                      "/advies TICKER – laatste advies met uitleg")
    elif cmd == "/overzicht":
        with SessionLocal() as s:
            pf = analysis.portfolio(s)
        lines = [f"<b>Portefeuille:</b> €{pf['total_eur']:,.0f} ({pf['pl_pct']:+.1f}%)\n"]
        for v in pf["positions"]:
            a = v["advice"]
            act = f"{telegram.EMOJI.get(a['action'], '')} {a['action']}" if a else "– nog geen advies"
            pl = f"{v['pl_pct']:+.1f}%" if v["pl_pct"] is not None else ""
            lines.append(f"<b>{v['ticker']}</b> {pl} · {act}")
        telegram.send("\n".join(lines))
    elif cmd == "/analyse":
        ok = analysis.run_in_background()
        telegram.send("Agents zijn gestart, je krijgt bericht bij gewijzigde adviezen." if ok else "Er loopt al een analyse.")
    elif cmd == "/advies":
        parts = text.split()
        if len(parts) < 2:
            telegram.send("Gebruik: /advies ASML.AS")
        else:
            with SessionLocal() as s:
                p = s.query(Position).filter(Position.ticker == parts[1].upper()).first()
                if not p or not p.advices:
                    telegram.send("Geen advies gevonden voor dat aandeel.")
                else:
                    telegram.send(telegram.format_advice(p.ticker, p.name, p.advices[0].to_dict(), None, analysis.app_url()))
    return {"ok": True}


# ------------------------------------------------------------------ frontend
@app.get("/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/{path:path}")
def spa(path: str):
    return FileResponse(STATIC / "index.html")
