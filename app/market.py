"""Koersen, indicatoren en nieuws ophalen (gratis bronnen, geen API-sleutel nodig).

- Koersen en historie: Yahoo Finance via yfinance
- Nieuws: Yahoo Finance + Google News RSS
"""
import logging
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import yfinance as yf

log = logging.getLogger("market")

_cache: dict = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ---------------------------------------------------------------- zoeken
def search_tickers(query: str):
    """Zoek tickers op naam of symbool (bijv. 'asml' -> ASML.AS, ASML)."""
    try:
        res = yf.Search(query, max_results=8, news_count=0)
        out = []
        for q in res.quotes:
            if q.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            out.append({
                "ticker": q.get("symbol"),
                "name": q.get("shortname") or q.get("longname") or q.get("symbol"),
                "exchange": q.get("exchDisp") or q.get("exchange", ""),
                "type": q.get("quoteType"),
            })
        return out
    except Exception as e:
        log.warning("zoeken mislukt: %s", e)
        return []


# ---------------------------------------------------------------- koersen
def get_quote(ticker: str):
    """Actuele koers + naam + valuta. 2 minuten gecachet."""
    def fetch():
        t = yf.Ticker(ticker)
        price, currency, name, prev = None, None, ticker, None
        try:
            fi = t.fast_info
            price = float(fi["last_price"])
            prev = float(fi["previous_close"]) if fi.get("previous_close") else None
            currency = fi.get("currency")
        except Exception:
            pass
        if price is None:
            h = t.history(period="5d")
            if not h.empty:
                price = float(h["Close"].iloc[-1])
                if len(h) > 1:
                    prev = float(h["Close"].iloc[-2])
        try:
            info = t.get_info()
            name = info.get("shortName") or info.get("longName") or ticker
            currency = currency or info.get("currency")
        except Exception:
            pass
        change = ((price / prev - 1) * 100) if price and prev else None
        return {"ticker": ticker, "price": price, "currency": (currency or "EUR").upper(),
                "name": name, "day_change_pct": change}
    try:
        return _cached(("q", ticker), 120, fetch)
    except Exception as e:
        log.warning("koers %s mislukt: %s", ticker, e)
        return {"ticker": ticker, "price": None, "currency": "EUR", "name": ticker, "day_change_pct": None}


def fx_to_eur(currency: str) -> float:
    """Wisselkoers naar euro, bijv. USD -> 0.92. Pence (GBp) wordt omgerekend."""
    currency = (currency or "EUR")
    if currency.upper() == "EUR":
        return 1.0
    factor = 1.0
    if currency == "GBp" or currency == "GBX":
        currency, factor = "GBP", 0.01
    def fetch():
        h = yf.Ticker(f"{currency.upper()}EUR=X").history(period="5d")
        return float(h["Close"].iloc[-1])
    try:
        return _cached(("fx", currency), 3600, fetch) * factor
    except Exception as e:
        log.warning("wisselkoers %s mislukt: %s", currency, e)
        return 1.0 * factor


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down
    return float((100 - 100 / (1 + rs)).iloc[-1])


def get_technicals(ticker: str):
    """Eenvoudige, begrijpelijke kengetallen voor de analist-agent."""
    def fetch():
        t = yf.Ticker(ticker)
        h = t.history(period="1y")
        if h.empty:
            return {}
        c = h["Close"]
        last = float(c.iloc[-1])
        def pct(days):
            return round((last / float(c.iloc[-days - 1]) - 1) * 100, 2) if len(c) > days else None
        out = {
            "laatste_koers": round(last, 4),
            "verandering_1w_pct": pct(5),
            "verandering_1m_pct": pct(21),
            "verandering_3m_pct": pct(63),
            "verandering_1j_pct": pct(len(c) - 1),
            "hoogste_52w": round(float(h["High"].max()), 4),
            "laagste_52w": round(float(h["Low"].min()), 4),
            "sma50": round(float(c.rolling(50).mean().iloc[-1]), 4) if len(c) >= 50 else None,
            "sma200": round(float(c.rolling(200).mean().iloc[-1]), 4) if len(c) >= 200 else None,
            "rsi14": round(_rsi(c), 1) if len(c) > 20 else None,
            "volatiliteit_dag_pct": round(float(c.pct_change().std() * 100), 2),
        }
        try:
            info = t.get_info()
            for k_src, k_dst in [("trailingPE", "koers_winst_verhouding"),
                                 ("forwardPE", "verwachte_kw_verhouding"),
                                 ("dividendYield", "dividendrendement"),
                                 ("targetMeanPrice", "koersdoel_analisten_gem"),
                                 ("recommendationKey", "advies_analisten"),
                                 ("sector", "sector"),
                                 ("marketCap", "beurswaarde")]:
                if info.get(k_src) is not None:
                    out[k_dst] = info[k_src]
        except Exception:
            pass
        return out
    try:
        return _cached(("tech", ticker), 1800, fetch)
    except Exception as e:
        log.warning("technicals %s mislukt: %s", ticker, e)
        return {}


def get_history(ticker: str, period="6mo"):
    """Slotkoersen voor het grafiekje in de app."""
    def fetch():
        h = yf.Ticker(ticker).history(period=period, interval="1d")
        return [{"d": idx.strftime("%Y-%m-%d"), "c": round(float(v), 4)} for idx, v in h["Close"].items()]
    try:
        return _cached(("hist", ticker, period), 1800, fetch)
    except Exception as e:
        log.warning("historie %s mislukt: %s", ticker, e)
        return []


# ---------------------------------------------------------------- nieuws
def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def get_news(ticker: str, name: str, days: int = 7, limit: int = 15):
    """Nieuws van de afgelopen dagen uit Yahoo Finance en Google News."""
    items = []
    # 1) Yahoo Finance
    try:
        for n in (yf.Ticker(ticker).news or [])[:20]:
            c = n.get("content", n)
            items.append({
                "titel": c.get("title", ""),
                "samenvatting": (c.get("summary") or "")[:400],
                "bron": (c.get("provider") or {}).get("displayName", "Yahoo Finance"),
                "url": (c.get("canonicalUrl") or {}).get("url") or c.get("link", ""),
                "datum": c.get("pubDate") or "",
            })
    except Exception as e:
        log.info("yahoo nieuws %s mislukt: %s", ticker, e)
    # 2) Google News (Engels + Nederlands)
    base = name.split(",")[0].replace(" N.V.", "").replace(" Inc.", "").strip() or ticker
    for q, hl, gl, ceid in [(f'"{base}" stock', "en-US", "US", "US:en"),
                            (f'"{base}" aandeel', "nl", "NL", "NL:nl")]:
        try:
            url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:7d")
                   + f"&hl={hl}&gl={gl}&ceid={ceid}")
            feed = feedparser.parse(url)
            for e in feed.entries[:10]:
                items.append({
                    "titel": e.get("title", ""),
                    "samenvatting": "",
                    "bron": (e.get("source") or {}).get("title", "Google News"),
                    "url": e.get("link", ""),
                    "datum": e.get("published", ""),
                })
        except Exception as ex:
            log.info("google nieuws mislukt: %s", ex)

    # filteren op datum, dubbele titels eruit, nieuwste eerst
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, out = set(), []
    for it in items:
        d = _parse_dt(it["datum"])
        if d and d < cutoff:
            continue
        key = it["titel"].lower()[:70]
        if not it["titel"] or key in seen:
            continue
        seen.add(key)
        it["_dt"] = d or datetime(1970, 1, 1, tzinfo=timezone.utc)
        out.append(it)
    out.sort(key=lambda x: x["_dt"], reverse=True)
    for it in out:
        d = it.pop("_dt")
        it["datum"] = d.strftime("%Y-%m-%d %H:%M") if d.year > 1970 else ""
    return out[:limit]
