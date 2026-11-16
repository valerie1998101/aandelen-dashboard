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

from . import analysis, bot, market, telegram
from .db import SessionLocal, Position, Trade, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")
# httpx logt standaard volledige URL's, inclusief het Telegram-token: dat willen we niet
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
    bot.start()  # Telegram-bot (alleen als TELEGRAM_BOT_TOKEN is ingesteld)


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown(wait=False)
    bot.stop()


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
    try:
        pid, _new = analysis.add_trade(body.ticker, body.side, body.quantity, body.price, body.fees,
                                       body.trade_date, body.name, body.notes)
    except analysis.TickerNotFound as e:
        raise HTTPException(400, f"Ticker {e} niet gevonden. Tip: Nederlandse aandelen eindigen op .AS (bijv. ASML.AS)")
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
