# 📈 Aandelen-dashboard

Een mobiele webapp waarin je je aandelen bijhoudt. Drie AI-agents volgen het nieuws en geven per aandeel een advies (**kopen, bijkopen, houden, afbouwen of verkopen**) en leggen uit **waarom**. Het hoofddoel: je **gemiddelde aankoopkoers verbeteren** door op het juiste moment bij te kopen en niet op dure momenten in te stappen.

> ⚠️ Dit is geen financieel advies. De agents kunnen zich vergissen. Jij beslist.

---

## Hoe het werkt

```
 Nieuws (Yahoo + Google News)          Koersen & kengetallen (Yahoo)
            │                                       │
            ▼                                       │
   ┌────────────────┐                               │
   │ 1. Nieuws-agent│  wat speelt er, raakt het     │
   │   (Haiku)      │  de kern van het bedrijf?     │
   └───────┬────────┘                               │
           ▼                                        ▼
   ┌────────────────────────────────────────────────────┐
   │ 2. Analist-agent (Sonnet)                           │
   │    trend, RSI, waardering → bijkoopzone & breekpunt │
   └───────┬────────────────────────────────────────────┘
           ▼
   ┌────────────────────────────────────────────────────┐
   │ 3. Risico-agent (Sonnet) – neemt het besluit        │
   │    • niet bijkopen als het nieuws de kern raakt     │
   │    • niet in een vallend mes grijpen                │
   │    • niet bijkopen als positie > 25% van portefeuille│
   │    • niet bijkopen na een sterke stijging (RSI > 70) │
   └───────┬────────────────────────────────────────────┘
           ▼
   Advies + "waarom" → dashboard op je telefoon (+ Telegram)
```

De agents draaien automatisch op werkdagen om **09:30 en 17:45** (instelbaar), en wanneer je op **↻ Analyseer nu** tikt.

### Wat zit er in de app
- **Overzicht**: totale waarde, winst/verlies, per aandeel het advies in één zin
- **Per aandeel**: advies met zekerheid, de *waarom*-punten, bijkoopzone en breekpunt, wat het advies zou veranderen, de verslagen van de nieuws- en analist-agent, en alle bronartikelen
- **Bijkoop-calculator**: "als ik X stuks koop op koers Y, wat wordt mijn nieuwe gemiddelde?" en "hoeveel moet ik bijkopen om mijn gemiddelde naar Z te krijgen?"
- **Meerdere aankopen en verkopen** per aandeel; de gemiddelde koers wordt automatisch berekend
- **Telegram**: een melding zodra een advies verandert, plus de commando's `/overzicht`, `/analyse` en `/advies ASML.AS`

---

## Online zetten op Railway (stap voor stap)

1. **Code op GitHub.** Deze map staat in de repo `aandelen-dashboard`.
2. Ga naar [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo** → kies `aandelen-dashboard`.
3. Klik in hetzelfde project op **+ Create** → **Database** → **PostgreSQL**. Zo blijven je gegevens bewaard.
4. Klik op je app-service → **Variables** en voeg toe:
   | Naam | Waarde |
   |---|---|
   | `ANTHROPIC_API_KEY` | je sleutel van console.anthropic.com |
   | `APP_PASSWORD` | een wachtwoord om in te loggen |
   | `SECRET_KEY` | een lange willekeurige tekst (bijv. 40 tekens typen) |
   | `DATABASE_URL` | klik **Add Reference** → kies Postgres → `DATABASE_URL` |
5. Ga naar **Settings** → **Networking** → **Generate Domain**. Je krijgt een adres als `aandelen-dashboard-production.up.railway.app`.
6. Open dat adres op je telefoon en log in.

### Op je beginscherm zetten
- **iPhone (Safari):** deelknop □↑ → *Zet op beginscherm*
- **Android (Chrome):** menu ⋮ → *App installeren*

Daarna opent hij als een gewone app, zonder adresbalk.

---

## Telegram koppelen (optioneel)

1. Open Telegram, zoek **@BotFather**, stuur `/newbot` en volg de stappen. Je krijgt een **token**.
2. Zet in Railway de variabele `TELEGRAM_BOT_TOKEN` = dat token. Railway herstart de app.
3. Stuur je nieuwe bot een bericht (bijv. `hoi`). Hij antwoordt met **je chat-id**.
4. Zet in Railway `TELEGRAM_CHAT_ID` = dat nummer. Klaar.

De bot reageert alleen op jouw chat. Wil je bij *elke* analyse een bericht in plaats van alleen bij wijzigingen? Zet dan `TELEGRAM_ALLEEN_WIJZIGINGEN=false`.

---

## Instellingen

| Variabele | Standaard | Wat |
|---|---|---|
| `ANALYSE_TIJDEN` | `09:30,17:45` | tijden op werkdagen; leeg = alleen handmatig |
| `TIJDZONE` | `Europe/Amsterdam` | |
| `MODEL_NIEUWS` | `claude-haiku-5-5` | goedkoper model voor het nieuws lezen |
| `MODEL_ANALIST` | `claude-sonnet-5-5` | |
| `MODEL_RISICO` | `claude-sonnet-5-5` | |
| `TELEGRAM_ALLEEN_WIJZIGINGEN` | `true` | |

**Kosten:** elke analyse per aandeel kost 3 AI-aanroepen. Met 10 aandelen en 2 analyses per dag zijn dat 60 aanroepen per dag. Houd je verbruik in de gaten op console.anthropic.com. Wil je het goedkoper? Kies één tijd per dag, of zet ook de analist op Haiku.

---

## Tickers

De app zoekt via Yahoo Finance. Tik een naam in en kies uit de lijst. Handig om te weten:
- Amsterdam: `.AS` → `ASML.AS`, `SHELL.AS`, `INGA.AS`, `ADYEN.AS`
- VS: zonder achtervoegsel → `AAPL`, `MSFT`, `NVDA`
- Frankfurt: `.DE`, Parijs: `.PA`, Londen: `.L`

Bedragen in dollars of ponden worden in het totaal automatisch omgerekend naar euro's.

---

## Lokaal draaien op Windows (optioneel)

```powershell
cd aandelen-dashboard
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:APP_PASSWORD="test"
uvicorn app.main:app --reload
```
Open daarna http://127.0.0.1:8000. Lokaal gebruikt hij een SQLite-bestand (`aandelen.db`).

## Mappen

```
app/
  main.py      web-app, API, planner, Telegram-commando's
  agents.py    de drie agents en hun opdrachten (hier pas je hun regels aan)
  analysis.py  portefeuille-berekening en de agent-pijplijn
  market.py    koersen, kengetallen en nieuws
  db.py        database (posities, transacties, adviezen)
  telegram.py  meldingen
  static/      de mobiele app (HTML/CSS/JS)
```
