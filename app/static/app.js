/* Aandelen-dashboard — eenvoudige app zonder framework. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const ACT_TXT = {
  KOPEN: "Kopen", BIJKOPEN: "Bijkopen", HOUDEN: "Houden", AFBOUWEN: "Afbouwen", VERKOPEN: "Verkopen",
};
let pollTimer = null;
let me = null;

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) { renderLogin(); throw new Error("login"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || "Er ging iets mis");
  return data;
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function money(v, cur = "EUR", digits) {
  if (v == null || isNaN(v)) return "–";
  let c = cur, f = 1;
  if (cur === "GBp" || cur === "GBX") { c = "GBP"; f = 0.01; }
  const d = digits ?? (Math.abs(v * f) >= 1000 ? 0 : 2);
  try { return new Intl.NumberFormat("nl-NL", { style: "currency", currency: c, minimumFractionDigits: d, maximumFractionDigits: d }).format(v * f); }
  catch { return v.toFixed(2) + " " + cur; }
}
function pct(v) { return v == null || isNaN(v) ? "–" : (v > 0 ? "+" : "") + v.toFixed(1).replace(".", ",") + "%"; }
function cls(v) { return v > 0 ? "up" : v < 0 ? "down" : ""; }
function num(v, d = 2) { return v == null ? "–" : Number(v).toLocaleString("nl-NL", { maximumFractionDigits: d }); }
function ago(iso) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "zojuist";
  if (m < 60) return `${m} min geleden`;
  if (m < 60 * 24) return `${Math.round(m / 60)} uur geleden`;
  return new Date(iso).toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
}
function badge(a) { return a ? `<span class="badge b-${a}">${ACT_TXT[a] || a}</span>` : `<span class="badge b-none">Geen advies</span>`; }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 3200);
}
function setTab(name) {
  $("#tabs").hidden = false;
  document.querySelectorAll("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === name));
}
function statusLine(st) {
  if (st?.running) return `<div class="status"><span class="dot"></span>Agents analyseren${st.current ? " " + esc(st.current) : ""}…</div>`;
  if (st?.last_error) return `<div class="status down">Laatste fout: ${esc(st.last_error)}</div>`;
  return "";
}
function watch(st, rerender) {
  clearTimeout(pollTimer);
  if (st?.running) pollTimer = setTimeout(async () => {
    const s = await api("/api/status").catch(() => null);
    if (s && !s.running) { rerender(); } else watch(s, rerender);
  }, 4000);
}

function spark(hist, avg, zone) {
  if (!hist || hist.length < 2) return "";
  const W = 600, H = 90, ys = hist.map(h => h.c);
  const extra = [avg, zone].filter(x => x > 0);
  const lo = Math.min(...ys, ...extra), hi = Math.max(...ys, ...extra);
  const y = v => H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8);
  const x = i => (i / (ys.length - 1)) * W;
  const d = ys.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const up = ys[ys.length - 1] >= ys[0];
  let lines = "";
  if (avg > 0) lines += `<line x1="0" x2="${W}" y1="${y(avg)}" y2="${y(avg)}" stroke="var(--muted)" stroke-dasharray="4 4" stroke-width="1.5"/>`;
  if (zone > 0) lines += `<line x1="0" x2="${W}" y1="${y(zone)}" y2="${y(zone)}" stroke="var(--buy)" stroke-dasharray="2 5" stroke-width="1.5"/>`;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${lines}
    <path d="${d}" fill="none" stroke="${up ? "var(--up)" : "var(--down)"}" stroke-width="2.2" vector-effect="non-scaling-stroke"/></svg>
    <div class="small muted">6 maanden · <span style="letter-spacing:-1px">- - -</span> jouw gem. koers${zone ? ' · <span class="up">····</span> bijkoopzone' : ""}</div>`;
}

// ---------------------------------------------------------------- login
function renderLogin() {
  $("#tabs").hidden = true;
  app.innerHTML = `<div class="login">
    <div class="logo">📈</div><h2>Aandelen-dashboard</h2>
    <form id="lf"><input type="password" id="pw" placeholder="Wachtwoord" autocomplete="current-password" autofocus>
    <div style="height:10px"></div><button>Inloggen</button></form></div>`;
  $("#lf").onsubmit = async e => {
    e.preventDefault();
    try { await api("/api/login", { method: "POST", body: { password: $("#pw").value } }); route(); }
    catch (err) { if (err.message !== "login") toast(err.message); }
  };
}

// ---------------------------------------------------------------- overzicht
async function renderHome() {
  setTab("home");
  const pf = await api("/api/portfolio");
  const ps = pf.positions;
  const advCount = ps.filter(p => p.advice && ["KOPEN", "BIJKOPEN", "AFBOUWEN", "VERKOPEN"].includes(p.advice.action)).length;
  app.innerHTML = `
    <h1>Mijn portefeuille</h1>
    <div class="hero">
      <div class="big num">${money(pf.total_eur, "EUR", 0)}</div>
      <div class="sub num ${cls(pf.pl_eur)}">${money(pf.pl_eur, "EUR", 0)} (${pct(pf.pl_pct)}) <span class="muted">t.o.v. inleg</span></div>
    </div>
    ${statusLine(pf.status)}
    ${ps.length ? `<div class="row" style="margin-bottom:10px">
        <div class="small muted">${advCount ? `<b style="color:var(--ink)">${advCount}</b> aandeel${advCount > 1 ? "en vragen" : " vraagt"} actie` : "Geen acties nodig"}</div>
        <button class="small ghost" id="runall" ${pf.status.running ? "disabled" : ""}>↻ Analyseer alles</button></div>` : ""}
    <div class="list">
      ${ps.map(p => `
        <a class="card" href="#/p/${p.id}">
          <div class="row pos-top">
            <div><div class="t">${esc(p.ticker)}</div><div class="n">${esc(p.name)}</div></div>
            <div class="p num"><div><b>${money(p.price, p.currency)}</b></div>
              <div class="small ${cls(p.pl_pct)}">${pct(p.pl_pct)} totaal</div></div>
          </div>
          <div class="headline">${badge(p.advice?.action)}
            <span>${p.advice ? esc(p.advice.headline) : '<span class="muted">Nog niet geanalyseerd</span>'}</span></div>
        </a>`).join("")}
    </div>
    ${ps.length ? "" : `<div class="empty">Nog geen aandelen.<br><br><a class="btn" href="#/nieuw" style="max-width:240px">+ Eerste aandeel toevoegen</a></div>`}
    ${me && !me.ai ? `<div class="card small down">ANTHROPIC_API_KEY ontbreekt: de agents kunnen nog geen advies geven.</div>` : ""}
    <div class="disclaimer">Adviezen worden gemaakt door AI-agents op basis van openbaar nieuws en koersdata. Geen financieel advies — jij beslist.</div>`;
  const b = $("#runall");
  if (b) b.onclick = async () => {
    try { await api("/api/analyse", { method: "POST", body: {} }); toast("Agents gestart"); renderHome(); }
    catch (e) { toast(e.message); }
  };
  watch(pf.status, renderHome);
}

// ---------------------------------------------------------------- toevoegen
function renderNew() {
  setTab("new");
  app.innerHTML = `
    <h1>Aandeel toevoegen</h1>
    <div class="card">
      <label>Zoek op naam of ticker</label>
      <input id="q" placeholder="bijv. ASML, Shell, Apple" autocomplete="off" autocapitalize="characters">
      <div class="sugg" id="sugg"></div>
      <div id="chosen" class="small muted"></div>
      <div class="grid2">
        <div><label>Aantal stuks</label><input id="qty" type="number" inputmode="decimal" step="any" placeholder="10"></div>
        <div><label>Aankoopkoers</label><input id="price" type="number" inputmode="decimal" step="any" placeholder="0,00"></div>
      </div>
      <div class="grid2">
        <div><label>Kosten (optioneel)</label><input id="fees" type="number" inputmode="decimal" step="any" value="0"></div>
        <div><label>Datum</label><input id="date" type="date" value="${new Date().toISOString().slice(0, 10)}"></div>
      </div>
      <label>Waarom heb je dit aandeel? (helpt de agents)</label>
      <textarea id="notes" rows="2" placeholder="bijv. lange termijn, dividend, groei in AI"></textarea>
      <div style="height:14px"></div>
      <button id="save">Toevoegen en laten analyseren</button>
      <p class="small muted">Tip: heb je het aandeel in meerdere keren gekocht? Voeg eerst één aankoop toe, daarna kun je bij het aandeel extra aankopen invoeren. De gemiddelde koers wordt automatisch berekend.</p>
    </div>`;
  let chosen = null, t = null;
  const q = $("#q");
  q.oninput = () => {
    chosen = null; $("#chosen").textContent = "";
    clearTimeout(t);
    const v = q.value.trim();
    if (v.length < 2) { $("#sugg").innerHTML = ""; return; }
    t = setTimeout(async () => {
      const res = await api("/api/search?q=" + encodeURIComponent(v)).catch(() => []);
      $("#sugg").innerHTML = res.map((r, i) => `<button data-i="${i}"><span><b>${esc(r.ticker)}</b> ${esc(r.name)}</span><span class="small muted">${esc(r.exchange)}</span></button>`).join("")
        || `<div class="small muted">Niets gevonden — je kunt de ticker ook direct invullen (Amsterdam: .AS, bijv. ASML.AS).</div>`;
      $("#sugg").querySelectorAll("button").forEach(b => b.onclick = async () => {
        chosen = res[b.dataset.i];
        q.value = chosen.ticker; $("#sugg").innerHTML = "";
        $("#chosen").textContent = chosen.name;
        const qu = await api("/api/quote/" + encodeURIComponent(chosen.ticker)).catch(() => null);
        if (qu?.price) {
          $("#chosen").textContent = `${chosen.name} · nu ${money(qu.price, qu.currency)}`;
          if (!$("#price").value) $("#price").value = qu.price.toFixed(2);
        }
        $("#qty").focus();
      });
    }, 300);
  };
  $("#save").onclick = async () => {
    const body = {
      ticker: q.value.trim(), name: chosen?.name,
      quantity: parseFloat($("#qty").value.replace(",", ".")) || 0,
      price: parseFloat($("#price").value.replace(",", ".")) || 0,
      fees: parseFloat($("#fees").value.replace(",", ".")) || 0,
      trade_date: $("#date").value || null, notes: $("#notes").value,
    };
    if (!body.ticker) return toast("Kies eerst een aandeel");
    if (body.quantity > 0 && !body.price) return toast("Vul de aankoopkoers in");
    $("#save").disabled = true;
    try { const r = await api("/api/positions", { method: "POST", body }); toast("Toegevoegd — agents zijn bezig"); location.hash = "#/p/" + r.id; }
    catch (e) { toast(e.message); $("#save").disabled = false; }
  };
}

// ---------------------------------------------------------------- detail
async function renderPosition(id) {
  setTab("");
  const p = await api("/api/positions/" + id);
  const a = p.advice;
  const cur = p.currency;
  app.innerHTML = `
    <div class="topbar"><a class="back" href="#/">‹ Overzicht</a>
      <button class="small ghost" id="run" ${p.status.running ? "disabled" : ""}>↻ Analyseer nu</button></div>
    <h1>${esc(p.name)}</h1>
    <div class="hero">
      <div class="big num">${money(p.price, cur)}</div>
      <div class="sub num"><span class="${cls(p.day_change_pct)}">${pct(p.day_change_pct)} vandaag</span> · <span class="muted">${esc(p.ticker)}</span></div>
      ${spark(p.history, p.avg_price, a?.buy_zone)}
    </div>
    ${statusLine(p.status)}

    ${a ? `
    <div class="card advice a-${a.action}">
      <div class="row"><div class="action" style="color:var(--${a.action === "HOUDEN" ? "hold" : a.action === "AFBOUWEN" ? "trim" : a.action === "VERKOPEN" ? "sell" : "buy"})">${ACT_TXT[a.action]}</div>
        <div style="text-align:right"><div class="small muted">zekerheid ${a.confidence}%</div><div class="meter"><i style="width:${a.confidence}%"></i></div></div></div>
      <div class="kop">${esc(a.headline)}</div>
      <h3 style="margin-top:4px">Waarom</h3>
      <ul class="why">${a.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
      ${(a.buy_zone || a.stop_level) ? `<div class="levels">
        ${a.buy_zone ? `<div class="level"><div class="l">Bijkopen interessant onder</div><div class="v num up">${money(a.buy_zone, cur)}</div></div>` : ""}
        ${a.stop_level ? `<div class="level"><div class="l">Verhaal breekt onder</div><div class="v num down">${money(a.stop_level, cur)}</div></div>` : ""}
      </div>` : ""}
      <div class="small muted" style="margin-top:10px">${ago(a.created_at)} · koers toen ${money(a.price_at_advice, cur)}</div>
    </div>
    <div class="card">
      <details open><summary>Wat zou het advies veranderen?</summary><div><ul class="plain">${a.change_triggers.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div></details>
      ${a.risk?.risicos?.length ? `<details><summary>Risico's</summary><div><ul class="plain">${a.risk.risicos.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div></details>` : ""}
      <details><summary>📰 Nieuws-agent ${a.news?.sentiment != null ? `<span class="small muted" style="margin-left:auto;margin-right:8px">sentiment ${a.news.sentiment > 0 ? "+" : ""}${a.news.sentiment}</span>` : ""}</summary><div>
        <p>${esc(a.news?.samenvatting)}</p>
        ${a.news?.these_in_gevaar ? `<p class="down"><b>⚠ Het nieuws raakt de kern van het bedrijf.</b></p>` : ""}
        <ul class="plain">${(a.news?.belangrijkste_gebeurtenissen || []).map(g => `<li><span class="${g.impact === "positief" ? "up" : g.impact === "negatief" ? "down" : ""}">●</span> ${esc(g.gebeurtenis)}</li>`).join("")}</ul>
      </div></details>
      <details><summary>📊 Analist-agent</summary><div>
        <p><b>Trend:</b> ${esc(a.analyst?.trend)} · <b>Waardering:</b> ${esc(a.analyst?.waardering)}</p>
        <p>${esc(a.analyst?.uitleg_techniek)}</p><p>${esc(a.analyst?.uitleg_waardering)}</p>
        <p class="small muted">Voorlopige mening analist: ${esc(ACT_TXT[a.analyst?.voorlopige_mening] || "")} — de risico-agent nam het eindbesluit.</p>
      </div></details>
      ${a.sources?.length ? `<details><summary>Bronnen (${a.sources.length})</summary><div class="news">
        ${a.sources.map((s, i) => `<a href="${esc(s.url)}" target="_blank" rel="noopener"><span class="src">[${i}] ${esc(s.bron)} · ${esc(s.datum)}</span><br>${esc(s.titel)}</a>`).join("")}
      </div></details>` : ""}
    </div>` : `<div class="card empty">Nog geen advies. ${p.status.running ? "De agents zijn bezig…" : "Tik op ‘Analyseer nu’."}</div>`}

    <h3>Mijn positie</h3>
    <div class="card"><div class="stats num">
      <div><div class="l">Aantal</div><div class="v">${num(p.quantity, 4)}</div></div>
      <div><div class="l">Gem. aankoopkoers</div><div class="v">${money(p.avg_price, cur)}</div></div>
      <div><div class="l">Waarde</div><div class="v">${money(p.value, cur)}</div></div>
      <div><div class="l">Winst / verlies</div><div class="v ${cls(p.pl)}">${money(p.pl, cur)} <span class="small">${pct(p.pl_pct)}</span></div></div>
      <div><div class="l">Aandeel in portefeuille</div><div class="v">${num(p.weight_pct, 1)}%</div></div>
      ${p.realized ? `<div><div class="l">Gerealiseerd</div><div class="v ${cls(p.realized)}">${money(p.realized, cur)}</div></div>` : ""}
    </div></div>

    <h3>Bijkoop-calculator</h3>
    <div class="card">
      <div class="seg"><button class="on" data-m="qty">Ik koop X stuks</button><button data-m="target">Doel-gemiddelde</button></div>
      <div class="grid2">
        <div><label>Koers bij aankoop</label><input id="cp" type="number" inputmode="decimal" step="any" value="${(a?.buy_zone && a.buy_zone < p.price ? a.buy_zone : p.price || 0).toFixed(2)}"></div>
        <div><label id="cl">Aantal stuks</label><input id="cx" type="number" inputmode="decimal" step="any" value="${Math.max(1, Math.round(p.quantity / 4))}"></div>
      </div>
      <div class="calc-out" id="co"></div>
    </div>

    <h3>Aankopen & verkopen</h3>
    <div class="card">
      ${p.trades.map(t => `<div class="trade num"><span>${new Date(t.date).toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "2-digit" })}</span>
        <span class="${t.side === "buy" ? "" : "down"}">${t.side === "buy" ? "+" : "−"}${num(t.quantity, 4)} × ${money(t.price, cur)}</span>
        <button data-del="${t.id}" aria-label="verwijder">×</button></div>`).join("") || '<div class="muted small">Nog geen aankopen.</div>'}
      <details style="margin-top:6px"><summary>+ Aankoop of verkoop toevoegen</summary><div>
        <div class="seg" id="side"><button class="on" data-s="buy">Gekocht</button><button data-s="sell">Verkocht</button></div>
        <div class="grid2"><div><label>Aantal</label><input id="tq" type="number" inputmode="decimal" step="any"></div>
          <div><label>Koers</label><input id="tp" type="number" inputmode="decimal" step="any" value="${p.price?.toFixed(2) || ""}"></div></div>
        <div class="grid2"><div><label>Kosten</label><input id="tf" type="number" inputmode="decimal" step="any" value="0"></div>
          <div><label>Datum</label><input id="td" type="date" value="${new Date().toISOString().slice(0, 10)}"></div></div>
        <div style="height:12px"></div><button id="addt">Opslaan</button>
      </div></details>
    </div>

    <h3>Jouw reden voor dit aandeel</h3>
    <div class="card"><textarea id="nt" rows="2" placeholder="De agents houden hier rekening mee">${esc(p.notes)}</textarea>
      <div style="height:8px"></div><button class="small ghost" id="savent">Notitie opslaan</button></div>

    ${p.advices.length > 1 ? `<h3>Eerdere adviezen</h3><div class="card">
      ${p.advices.slice(1).map(h => `<div class="hist">${badge(h.action)}<span style="flex:1">${esc(h.headline)}</span><span class="small muted" style="white-space:nowrap">${ago(h.created_at)}</span></div>`).join("")}
    </div>` : ""}

    <div style="height:20px"></div><button class="danger" id="delp">Aandeel verwijderen</button>
    <div class="disclaimer">Geen financieel advies. De agents kunnen zich vergissen — controleer belangrijk nieuws zelf.</div>`;

  // analyseer nu
  $("#run").onclick = async () => {
    try { await api("/api/analyse", { method: "POST", body: { position_id: p.id } }); toast("Agents gestart (±30 sec)"); renderPosition(id); }
    catch (e) { toast(e.message); }
  };

  // calculator
  let mode = "qty";
  const calc = () => {
    const cp = parseFloat($("#cp").value), x = parseFloat($("#cx").value);
    const q = p.quantity, avg = p.avg_price, out = $("#co");
    if (!(cp > 0) || !(x > 0)) { out.innerHTML = "Vul koers en aantal in."; return; }
    if (mode === "qty") {
      const nq = q + x, navg = (q * avg + x * cp) / nq, diff = navg - avg;
      out.innerHTML = `Nieuwe gemiddelde koers<div class="big num">${money(navg, cur)}</div>
        <span class="${diff < 0 ? "up" : "down"}">${diff < 0 ? "▼" : "▲"} ${money(Math.abs(diff), cur)} (${pct(avg ? diff / avg * 100 : 0)})</span> ·
        kost ${money(x * cp, cur)} · dan ${num(nq, 4)} stuks`;
    } else {
      if (!(cp < x && x < avg)) { out.innerHTML = `Het doel-gemiddelde moet tussen de aankoopkoers (${money(cp, cur)}) en je huidige gemiddelde (${money(avg, cur)}) liggen.`; return; }
      const need = q * (avg - x) / (x - cp);
      out.innerHTML = `Je moet bijkopen<div class="big num">${num(Math.ceil(need), 0)} stuks</div>
        kost ${money(Math.ceil(need) * cp, cur)} om je gemiddelde naar ${money(x, cur)} te brengen`;
    }
  };
  document.querySelectorAll(".card .seg:not(#side) button").forEach(b => b.onclick = () => {
    mode = b.dataset.m;
    b.parentElement.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    $("#cl").textContent = mode === "qty" ? "Aantal stuks" : "Doel-gemiddelde";
    $("#cx").value = mode === "qty" ? Math.max(1, Math.round(p.quantity / 4)) : (p.avg_price * 0.95).toFixed(2);
    calc();
  });
  $("#cp").oninput = $("#cx").oninput = calc; calc();

  // trades
  let side = "buy";
  $("#side").querySelectorAll("button").forEach(b => b.onclick = () => {
    side = b.dataset.s; $("#side").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  });
  $("#addt").onclick = async () => {
    const body = { side, quantity: parseFloat($("#tq").value), price: parseFloat($("#tp").value),
      fees: parseFloat($("#tf").value) || 0, trade_date: $("#td").value || null };
    if (!(body.quantity > 0 && body.price > 0)) return toast("Vul aantal en koers in");
    try { await api(`/api/positions/${p.id}/trades`, { method: "POST", body }); toast("Opgeslagen"); renderPosition(id); }
    catch (e) { toast(e.message); }
  };
  document.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Deze transactie verwijderen?")) return;
    await api("/api/trades/" + b.dataset.del, { method: "DELETE" }); renderPosition(id);
  });
  $("#savent").onclick = async () => { await api("/api/positions/" + p.id, { method: "PATCH", body: { notes: $("#nt").value } }); toast("Opgeslagen"); };
  $("#delp").onclick = async () => {
    if (!confirm(`${p.ticker} en alle adviezen verwijderen?`)) return;
    await api("/api/positions/" + p.id, { method: "DELETE" }); location.hash = "#/";
  };
  watch(p.status, () => renderPosition(id));
}

// ---------------------------------------------------------------- router
async function route(scroll = true) {
  clearTimeout(pollTimer);
  const h = location.hash.replace(/^#/, "") || "/";
  try {
    if (!me) me = await api("/api/me");
    if (h === "/nieuw") renderNew();
    else if (h.startsWith("/p/")) await renderPosition(h.slice(3));
    else await renderHome();
    if (scroll) window.scrollTo(0, 0);
  } catch (e) {
    if (e.message !== "login") app.innerHTML = `<div class="empty">${esc(e.message)}<br><br><button class="small ghost" onclick="route()">Opnieuw</button></div>`;
  }
}
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden) route(false); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
