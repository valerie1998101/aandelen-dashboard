"""De drie agents die samen een advies maken.

    Nieuws-agent  ->  Analist-agent  ->  Risico-agent (neemt het besluit)

Elke agent is één Claude-aanroep met een vaste opdracht. Via "tool use"
dwingen we af dat het antwoord netjes gestructureerd terugkomt (JSON),
zodat de app het kan tonen.
"""
import json
import logging
import os

import anthropic

log = logging.getLogger("agents")

MODEL_NEWS = os.getenv("MODEL_NIEUWS", "claude-haiku-5-5")
MODEL_ANALYST = os.getenv("MODEL_ANALIST", "claude-sonnet-5-5")
MODEL_RISK = os.getenv("MODEL_RISICO", "claude-sonnet-5-5")

ACTIONS = ["KOPEN", "BIJKOPEN", "HOUDEN", "AFBOUWEN", "VERKOPEN"]

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # leest ANTHROPIC_API_KEY
    return _client


def explain_error(ex) -> str:
    """Zet een API-fout om in een begrijpelijke zin."""
    msg = str(ex)
    low = msg.lower()
    if "credit balance" in low or "billing" in low:
        return "Je Anthropic-tegoed is op of te laag. Voeg tegoed toe op console.anthropic.com → Billing."
    if isinstance(ex, anthropic.AuthenticationError) or "authentication" in low or "x-api-key" in low:
        return "De ANTHROPIC_API_KEY klopt niet. Maak een nieuwe aan en zet die in Railway."
    if isinstance(ex, anthropic.NotFoundError) or "model" in low and "not" in low:
        return f"Het AI-model bestaat niet of je hebt er geen toegang toe ({msg[:150]})."
    if isinstance(ex, anthropic.RateLimitError):
        return "Te veel aanvragen tegelijk bij Anthropic. Probeer het over een minuut opnieuw."
    if "api_key" in low or "api key" in low:
        return "ANTHROPIC_API_KEY ontbreekt of is leeg in Railway."
    return msg[:300]


def _call(model, system, user, tool_name, schema, max_tokens=1500):
    resp = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"name": tool_name, "description": "Lever je resultaat aan.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Agent gaf geen gestructureerd antwoord")


# ------------------------------------------------------------------ 1. Nieuws
NEWS_SYSTEM = """Je bent de Nieuws-agent in een beleggingsteam. Je leest de nieuwsberichten
van de afgelopen week over één aandeel en vat samen wat er ECHT toe doet voor de koers
op middellange termijn (weken tot maanden). Negeer clickbait, koersdoel-herhalingen zonder
inhoud en algemene marktpraat. Wees nuchter: geen nieuws is ook een conclusie.
Schrijf in eenvoudig Nederlands, zonder jargon. Verwijs naar berichten met hun nummer [n]."""

NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "integer", "minimum": -2, "maximum": 2,
                      "description": "-2 zeer negatief, 0 neutraal, 2 zeer positief"},
        "samenvatting": {"type": "string", "description": "2-3 zinnen: wat speelt er?"},
        "belangrijkste_gebeurtenissen": {
            "type": "array", "maxItems": 4,
            "items": {"type": "object", "properties": {
                "gebeurtenis": {"type": "string"},
                "impact": {"type": "string", "enum": ["positief", "negatief", "neutraal"]},
                "bron_nummers": {"type": "array", "items": {"type": "integer"}},
            }, "required": ["gebeurtenis", "impact"]},
        },
        "these_in_gevaar": {"type": "boolean",
                            "description": "Is er nieuws dat het fundamentele verhaal van het bedrijf aantast "
                                           "(fraude, winstwaarschuwing, verlies grote klant, schuldenproblemen)?"},
    },
    "required": ["sentiment", "samenvatting", "belangrijkste_gebeurtenissen", "these_in_gevaar"],
}


def news_agent(ticker, name, news):
    if not news:
        return {"sentiment": 0, "samenvatting": "Geen relevant nieuws gevonden in de afgelopen week.",
                "belangrijkste_gebeurtenissen": [], "these_in_gevaar": False}
    lines = [f"[{i}] {n['datum']} | {n['bron']} | {n['titel']}" + (f" — {n['samenvatting']}" if n['samenvatting'] else "")
             for i, n in enumerate(news)]
    user = f"Aandeel: {name} ({ticker})\n\nNieuwsberichten:\n" + "\n".join(lines)
    return _call(MODEL_NEWS, NEWS_SYSTEM, user, "nieuws_rapport", NEWS_SCHEMA, 1200)


# ------------------------------------------------------------------ 2. Analist
ANALYST_SYSTEM = """Je bent de Analist-agent. Je krijgt kengetallen van een aandeel, de positie van
de belegger (aantal, gemiddelde aankoopkoers) en het rapport van de Nieuws-agent.
Het hoofddoel van deze belegger: zijn GEMIDDELDE AANKOOPKOERS verbeteren door slim bij te kopen
op goede momenten, en niet in te stappen op dure momenten.
Beoordeel: trend (koers t.o.v. SMA50/SMA200), of het aandeel overkocht/oververkocht is (RSI),
waar het staat binnen de 52-weeks bandbreedte, en de waardering als die bekend is.
Bepaal een concrete 'bijkoopzone' (een koers) waar bijkopen het gemiddelde zinvol verlaagt
ZONDER in een vallend mes te grijpen. Leg alles uit in eenvoudig Nederlands."""

ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "trend": {"type": "string", "enum": ["stijgend", "zijwaarts", "dalend"]},
        "waardering": {"type": "string", "enum": ["goedkoop", "redelijk", "duur", "onbekend"]},
        "uitleg_techniek": {"type": "string", "description": "2-3 zinnen over trend en momentum"},
        "uitleg_waardering": {"type": "string", "description": "1-2 zinnen"},
        "bijkoopzone": {"type": ["number", "null"], "description": "Koers waaronder bijkopen interessant is"},
        "breekpunt": {"type": ["number", "null"],
                      "description": "Koers waaronder de trend echt kapot is (bijv. onder laagste steun)"},
        "voorlopige_mening": {"type": "string", "enum": ACTIONS},
    },
    "required": ["trend", "waardering", "uitleg_techniek", "uitleg_waardering", "voorlopige_mening"],
}


def analyst_agent(ticker, name, technicals, position, news_report):
    user = json.dumps({
        "aandeel": f"{name} ({ticker})",
        "kengetallen": technicals,
        "positie_belegger": position,
        "nieuws_rapport": news_report,
    }, ensure_ascii=False, default=str, indent=1)
    return _call(MODEL_ANALYST, ANALYST_SYSTEM, user, "analyse", ANALYST_SCHEMA, 1500)


# ------------------------------------------------------------------ 3. Risico / besluit
RISK_SYSTEM = """Je bent de Risico-agent en je neemt het eindbesluit. Je bent de kritische stem in het team:
je kijkt of het advies van de Analist verstandig is voor DEZE belegger.

Regels die je altijd toepast:
- Bijkopen om het gemiddelde te verlagen is alleen goed als het verhaal van het bedrijf nog klopt.
  Als de Nieuws-agent zegt dat de these in gevaar is: GEEN bijkopen, overweeg AFBOUWEN/VERKOPEN.
- Grijp niet in een vallend mes: bij een dalende trend én negatief nieuws -> eerst stabilisatie afwachten.
- Als één positie meer dan 25% van de portefeuille is: geen bijkopen, waarschuw voor concentratie.
- Bij een sterke stijging en RSI > 70: niet bijkopen op deze koers (dat verhoogt je gemiddelde),
  wacht op een terugval richting de bijkoopzone.
- Wees eerlijk over onzekerheid. 'HOUDEN' is vaak het juiste antwoord.

Acties: KOPEN (nieuwe positie / sterk instapmoment), BIJKOPEN (bestaande positie uitbreiden),
HOUDEN, AFBOUWEN (deel verkopen), VERKOPEN (alles verkopen).
Schrijf voor een beginnende belegger: kort, concreet, zonder jargon. De 'waarom'-punten zijn het
belangrijkste deel: elk punt is één heldere zin."""

RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "actie": {"type": "string", "enum": ACTIONS},
        "zekerheid": {"type": "integer", "minimum": 0, "maximum": 100},
        "kop": {"type": "string", "description": "Eén zin advies, bijv. 'Houden, bijkopen pas onder €42'"},
        "waarom": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"}},
        "wat_verandert_het_advies": {"type": "array", "maxItems": 3, "items": {"type": "string"},
                                     "description": "Concrete signalen waardoor je van mening verandert"},
        "risicos": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "bijkoopzone": {"type": ["number", "null"]},
        "breekpunt": {"type": ["number", "null"]},
    },
    "required": ["actie", "zekerheid", "kop", "waarom", "wat_verandert_het_advies", "risicos"],
}


def risk_agent(ticker, name, technicals, position, portfolio_weight_pct, news_report, analysis):
    user = json.dumps({
        "aandeel": f"{name} ({ticker})",
        "positie_belegger": position,
        "gewicht_in_portefeuille_pct": portfolio_weight_pct,
        "kengetallen": technicals,
        "nieuws_rapport": news_report,
        "analist_rapport": analysis,
    }, ensure_ascii=False, default=str, indent=1)
    return _call(MODEL_RISK, RISK_SYSTEM, user, "besluit", RISK_SCHEMA, 1500)
