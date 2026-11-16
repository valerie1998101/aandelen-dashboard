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
   Advies + "waarom" → Telegram (+ webdashboard)
```

De agents draaien automatisch op werkdagen om **09:30 en 17:45** (instelbaar), en wanneer je op **↻ Analyseer nu** tikt.

### Wat zit er in de app
- **Overzicht**: totale waarde, winst/verlies, per aandeel het advies in één zin
- **Per aandeel**: advies met zekerheid, de *waarom*-punten, bijkoopzone en breekpunt, wat het advies zou veranderen, de verslagen van de nieuws- en analist-agent, en alle bronartikelen
- **Bijkoop-calculator**: "als ik X stuks koop op koers Y, wat wordt mijn nieuwe gemiddelde?" en "hoeveel moet ik bijkopen om mijn gemiddelde naar Z te krijgen?"
- **Meerdere aankopen en verkopen** per aandeel; de gemiddelde koers wordt automatisch berekend
- **Alles via Telegram**: aandelen toevoegen in gewone taal, adviezen met knoppen voor de onderbouwing, de bijkoop-calculator, en een melding zodra een advies verandert

---

## Gebruiken via Telegram (aanrader)

Je kunt alles vanuit Telegram doen. Typ gewoon een zin:

| Jij typt | De bot doet |
|---|---|
| *ik heb 10 ASML gekocht voor 612* | vraagt "klopt dit?" → slaat op → agents maken meteen een advies |
| *wat moet ik met Shell?* | advies + waarom, met knoppen 📰 Nieuws · 📊 Analist · 🔗 Bronnen · 🧮 Bijkopen? |
| *wat wordt mijn gemiddelde als ik 5 ASML bijkoop op 590?* | rekent je nieuwe gemiddelde uit |
| *hoeveel moet ik bijkopen om op 600 gemiddeld te komen bij ASML?* | aantal stuks + kosten |
| */overzicht* | hele portefeuille met adviezen |

Alle commando's: `/overzicht` `/advies` `/koop` `/verkoop` `/bijkoop` `/doel` `/analyse` `/transacties` `/notitie` `/verwijder` `/help`.

Als een advies verandert, krijg je vanzelf een bericht.

---

## Online zetten op Railway (stap voor stap)

### 1. Telegram-bot maken (2 minuten)
1. Open Telegram, zoek **@BotFather** en stuur `/newbot`.
2. Kies een naam (bijv. *Mijn Aandelen*) en een gebruikersnaam die eindigt op `bot`.
3. Je krijgt een **token** (iets als `7123456789:AAH...`). Bewaar die.

### 2. Railway
1. Ga naar [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo** → kies `aandelen-dashboard`.
2. Klik in hetzelfde project op **+ Create** → **Database** → **PostgreSQL**. Zo blijven je gegevens bewaard.
3. Klik op je app-service → **Variables** en voeg toe:
   | Naam | Waarde |
   |---|---|
   | `ANTHROPIC_API_KEY` | je sleutel van console.anthropic.com |
   | `TELEGRAM_BOT_TOKEN` | het token van BotFather |
   | `SECRET_KEY` | een lange willekeurige tekst |
   | `DATABASE_URL` | klik **Add Reference** → kies Postgres → `DATABASE_URL` |
4. Wacht tot de deploy klaar is (groen).

### 3. Koppelen
1. Stuur je bot in Telegram een bericht, bijv. `hoi`. Hij antwoordt met **je chat-id**.
2. Zet in Railway de variabele `TELEGRAM_CHAT_ID` = dat nummer. Railway herstart vanzelf.
3. Stuur `/help`. Klaar! Vanaf nu reageert de bot alleen op jou.

### Ook het webdashboard gebruiken? (optioneel)
Telegram is genoeg, maar het dashboard geeft een mooi overzicht met grafiek en calculator.
1. Zet de variabele `APP_PASSWORD` (je inlogwachtwoord).
2. **Settings** → **Networking** → **Generate Domain**, en open dat adres op je telefoon.
3. Zet hem op je beginscherm. Op een iPhone: in Safari de deelknop □↑ → *Zet op beginscherm*. Op Android: in Chrome ⋮ → *App installeren*.

> Zet altijd een `APP_PASSWORD` als je een domein aanmaakt, anders kan iedereen met het adres erbij.

Wil je bij *elke* analyse een bericht in plaats van alleen bij wijzigingen? Zet `TELEGRAM_ALLEEN_WIJZIGINGEN=false`.

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
  main.py      web-app, API en planner
  bot.py       Telegram-bot (commando's, knoppen, gewone zinnen)
  agents.py    de drie agents en hun opdrachten (hier pas je hun regels aan)
  analysis.py  portefeuille-berekening en de agent-pijplijn
  market.py    koersen, kengetallen en nieuws
  db.py        database (posities, transacties, adviezen)
  telegram.py  berichten versturen en opmaken
  static/      de mobiele app (HTML/CSS/JS)
```
