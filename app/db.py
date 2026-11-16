"""Database: posities, aankopen/verkopen en adviezen.

Op Railway gebruikt de app automatisch Postgres (DATABASE_URL).
Lokaal valt hij terug op een SQLite-bestand.
"""
import json
import os
from datetime import datetime, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Date, Text,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aandelen.db")
# Railway geeft postgres:// of postgresql:// — wij gebruiken expliciet de psycopg2-driver
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(prefix):]

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(32), unique=True, nullable=False)
    name = Column(String(200), default="")
    currency = Column(String(8), default="EUR")
    notes = Column(Text, default="")  # jouw eigen reden om het aandeel te hebben
    created_at = Column(DateTime, default=datetime.utcnow)

    trades = relationship("Trade", back_populates="position", cascade="all, delete-orphan",
                          order_by="Trade.trade_date")
    advices = relationship("Advice", back_populates="position", cascade="all, delete-orphan",
                           order_by="Advice.created_at.desc()")

    def stats(self):
        """Aantal stuks, gemiddelde aankoopkoers en geïnvesteerd bedrag.

        Kopen verhoogt het aantal en herberekent het gemiddelde.
        Verkopen verlaagt het aantal tegen het bestaande gemiddelde
        (het gemiddelde zelf verandert dan niet).
        """
        qty = 0.0
        cost = 0.0
        realized = 0.0
        for t in self.trades:
            if t.side == "buy":
                cost += t.quantity * t.price + (t.fees or 0)
                qty += t.quantity
            else:
                avg = cost / qty if qty else 0
                sell_q = min(t.quantity, qty)
                realized += sell_q * (t.price - avg) - (t.fees or 0)
                cost -= sell_q * avg
                qty -= sell_q
        avg = cost / qty if qty > 1e-9 else 0.0
        return {"quantity": round(qty, 6), "avg_price": avg, "invested": cost, "realized": realized}


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
    side = Column(String(4), default="buy")  # buy | sell
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fees = Column(Float, default=0.0)
    trade_date = Column(Date, default=date.today)
    position = relationship("Position", back_populates="trades")


class Advice(Base):
    __tablename__ = "advices"
    id = Column(Integer, primary_key=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    action = Column(String(16))          # KOPEN | BIJKOPEN | HOUDEN | AFBOUWEN | VERKOPEN
    confidence = Column(Integer)         # 0-100
    headline = Column(String(300))       # één zin
    reasons_json = Column(Text)          # lijst met "waarom"-punten
    change_triggers_json = Column(Text)  # wat zou het advies veranderen
    buy_zone = Column(Float, nullable=True)   # koers waaronder bijkopen interessant is
    stop_level = Column(Float, nullable=True) # koers waaronder de these breekt
    price_at_advice = Column(Float)
    news_json = Column(Text)             # output nieuws-agent
    analyst_json = Column(Text)          # output analist-agent
    risk_json = Column(Text)             # output risico-agent
    sources_json = Column(Text)          # gebruikte nieuwsartikelen
    position = relationship("Position", back_populates="advices")

    def to_dict(self):
        def j(x, default):
            try:
                return json.loads(x) if x else default
            except Exception:
                return default
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "action": self.action,
            "confidence": self.confidence,
            "headline": self.headline,
            "reasons": j(self.reasons_json, []),
            "change_triggers": j(self.change_triggers_json, []),
            "buy_zone": self.buy_zone,
            "stop_level": self.stop_level,
            "price_at_advice": self.price_at_advice,
            "news": j(self.news_json, {}),
            "analyst": j(self.analyst_json, {}),
            "risk": j(self.risk_json, {}),
            "sources": j(self.sources_json, []),
        }


def init_db():
    Base.metadata.create_all(engine)
