// Panel: araç çubuğu, tarama, özet, setup kartları ve ayrıntı (grafik + kural kontrolleri).
import { runScan, detect, sortSetups } from '../scan/scanner.js';
import { pickSetups, activePlans, riskPct, feeR } from '../scan/wave3.js';
import { pickTriangles } from '../scan/triangle.js';
import { loadSettings, saveSettings, renderSettings, toScanOptions, UI_DEFAULTS } from './settings.js';
import { drawSetup, drawTriangle, destroyChart, LEGEND, TRI_LEGEND } from './chart.js';
import { triCard, triDetail, triSummary, triKey } from './triangleView.js';
import { fmtPrice, fmtPct, fmtX, fmtTime, esc } from './format.js';

const $ = (sel) => document.querySelector(sel);
const NET_KEYS = ['timeframe', 'topN', 'manualCoins'];

const state = {
  settings: loadSettings(),
  scan: null, // runScan sonucu (mumlar önbellekte)
  setups: [],
  diagnostics: [],
  selected: null,
  busy: false,
  abort: null,
  timer: null,
  seenKeys: null,
};

const isTri = () => state.settings.mode === 'triangle';
const setupKey = (s) => (s.kind === 'triangle' ? triKey(s) : `${s.coin}|${s.direction}|${s.phase}|${s.points.p0.price}|${s.points.p1.price}`);

// ------------------------------------------------------------------ araç çubuğu
function syncToolbar() {
  const s = state.settings;
  for (const b of document.querySelectorAll('#tf button')) b.setAttribute('aria-pressed', String(b.dataset.tf === s.timeframe));
  $('#ruleSet').value = s.ruleSet;
  $('#entryType').value = s.entryType;
  $('#showAll').checked = !!s.showAll;
  $('#onlyPerfect').checked = !!s.onlyPerfect;
  $('#mode').value = s.mode;
  // Dalga-3'e özgü kontroller üçgen modunda gizlenir
  document.body.classList.toggle('mode-triangle', s.mode === 'triangle');
}

function updateSettings(patch) {
  const prev = state.settings;
  state.settings = patch === null ? { ...UI_DEFAULTS } : { ...prev, ...patch };
  saveSettings(state.settings);
  syncToolbar();
  if (patch === null) renderSettings($('#settings'), state.settings, updateSettings);
  if ('autoRefresh' in (patch || {})) setupAutoRefresh();
  const netChanged = patch === null || NET_KEYS.some((k) => k in patch && String(patch[k]) !== String(prev[k]));
  if (!state.scan) { if (state.busy) scan(); return; } // ilk tarama sürerken değişiklik: yeniden başlat
  if (netChanged) { markStale(); scan(); }
  else recompute();
}

// ------------------------------------------------------------------ tarama
async function scan({ silent = false } = {}) {
  if (state.busy) state.abort?.abort();
  const ctrl = new AbortController();
  state.abort = ctrl;
  state.busy = true;
  $('#scanBtn').disabled = true;
  $('#scanBtn').textContent = 'Taranıyor…';
  const prog = $('#progress');
  prog.hidden = false;
  setProgress(0, 0, 'Coin listesi alınıyor…');
  try {
    const opts = toScanOptions(state.settings);
    const diagnostics = [];
    const res = await runScan({ ...opts, diagnostics }, {
      signal: ctrl.signal,
      onProgress: ({ done, total, coin }) => setProgress(done, total, coin ? `${coin} (${done}/${total})` : `0/${total}`),
      onRetry: ({ attempt, wait }) => setProgress(null, null, `OKX yoğun, yeniden deneniyor (${attempt}. deneme, ${Math.round(wait / 1000)} sn)…`),
    });
    if (ctrl.signal.aborted) return;
    state.scan = res;
    state.diagnostics = diagnostics;
    state.setups = res.setups;
    notifyNew(silent);
    render();
  } catch (err) {
    if (err.name === 'AbortError' && ctrl.signal.aborted) return; // yerine yeni tarama başladı
    $('#summary').innerHTML = `<p class="error">Tarama başarısız: ${esc(err.message)}</p>`;
  } finally {
    if (state.abort === ctrl) {
      state.busy = false;
      $('#scanBtn').disabled = false;
      $('#scanBtn').textContent = 'Tara';
      prog.hidden = true;
    }
  }
}

/** Ağ gerektirmeyen ayar değişikliği: önbellekteki mumlar üzerinde algılamayı yeniden çalıştır. */
function recompute() {
  const opts = toScanOptions(state.settings);
  const diagnostics = [];
  const setups = [];
  for (const r of state.scan.results) {
    if (!r.candles.length) continue;
    r.setups = detect(r, { ...opts, diagnostics });
    for (const s of r.setups) setups.push({ ...s, coin: r.coin, instId: r.instId });
  }
  sortSetups(setups, opts);
  state.setups = setups;
  state.diagnostics = diagnostics;
  render();
}

/** Zaman dilimi / coin listesi değişti: eski sonuçlar yeni tarama bitene kadar soluk gösterilir. */
function markStale() {
  document.querySelector('.layout').classList.add('stale');
  $('#summary').innerHTML = `<p class="muted">${esc(state.settings.timeframe)} taranıyor… Aşağıdaki sonuçlar önceki taramaya ait.</p>`;
}

function setProgress(done, total, text) {
  if (done != null && total) $('#progressBar').style.width = `${Math.round((100 * done) / total)}%`;
  if (done === 0) $('#progressBar').style.width = '0%';
  $('#progressText').textContent = text;
}

// ------------------------------------------------------------------ otomatik yenileme + bildirim
function setupAutoRefresh() {
  clearInterval(state.timer);
  state.timer = null;
  const min = Number(state.settings.autoRefresh) || 0;
  if (!min) return;
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
  state.timer = setInterval(() => { if (!state.busy) scan({ silent: true }); }, min * 60e3);
}

function notifyNew(silent) {
  if (isTri()) return notifyTriangles(silent);
  const vis = pickSetups(state.setups, toScanOptions(state.settings));
  const keys = new Set(vis.map(setupKey));
  const approaching = new Set(vis.filter((s) => s.zone?.state === 'approaching').map(setupKey));
  const prev = state.seenKeys, prevApp = state.seenApproaching;
  state.seenKeys = keys;
  state.seenApproaching = approaching;
  if (!silent || !prev) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const line = (s) => `${s.zone?.perfect ? '★ ' : ''}${s.coin} ${dirTxt(s)} · ${planSummary(s)}`;
  const fresh = vis.filter((s) => !prev.has(setupKey(s)));
  // Emre yaklaşanlar ve mükemmel setup'lar ayrı bildirim: kaçırılmasın.
  const near = vis.filter((s) => approaching.has(setupKey(s)) && !prevApp?.has(setupKey(s)));
  const perfect = fresh.filter((s) => s.zone?.perfect);
  const other = fresh.filter((s) => !s.zone?.perfect);
  const tf = state.scan.tf.id;
  try {
    if (near.length) new Notification(`Fiyat emre yaklaştı (${tf})`, { body: near.slice(0, 5).map((s) => `${line(s)} · emir ${fmtPrice(s.zone.legs[0], s.price)}`).join('\n') });
    if (perfect.length) new Notification(`★ Yeni mükemmel setup (${tf})`, { body: perfect.slice(0, 5).map((s) => `${line(s)} · seviye ${fmtPrice(s.zone.confluence, s.price)}`).join('\n') });
    if (other.length) new Notification(`Yeni dalga-3 setup (${tf})`, { body: other.slice(0, 5).map(line).join('\n') });
  } catch { /* bazı tarayıcılarda yalnızca SW ile */ }
}

/** Üçgen modu bildirimi: yeni kırılımlar ve kırılıma yaklaşanlar. */
function notifyTriangles(silent) {
  const vis = visibleSetups();
  const keyed = (st) => new Set(vis.filter((t) => t.state === st).map(setupKey));
  const broken = keyed('broken'), near = keyed('near');
  const prevB = state.seenBroken, prevN = state.seenNear;
  state.seenBroken = broken;
  state.seenNear = near;
  if (!silent || !prevB) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const tf = state.scan.tf.id;
  const line = (t) => `${t.coin} ${t.typeLabel} · ${t.contextLabel} · R:R ${fmtX(t.rr, 1)}`;
  const nb = vis.filter((t) => broken.has(setupKey(t)) && !prevB.has(setupKey(t)));
  const nn = vis.filter((t) => near.has(setupKey(t)) && !prevN?.has(setupKey(t)));
  try {
    if (nb.length) new Notification(`Üçgen kırıldı (${tf})`, { body: nb.slice(0, 5).map(line).join('\n') });
    if (nn.length) new Notification(`Üçgen kırılıma yakın (${tf})`, { body: nn.slice(0, 5).map(line).join('\n') });
  } catch { /* yoksay */ }
}

// ------------------------------------------------------------------ çizim
const useBreak = () => state.settings.entryType !== 'zone';
const useZone = () => state.settings.entryType !== 'break';
const dirTxt = (s) => (s.direction === 'long' ? 'LONG ▲' : 'SHORT ▼');

function visibleSetups() {
  if (isTri()) return state.settings.showAll ? state.setups : pickTriangles(state.setups, toScanOptions(state.settings));
  if (!state.settings.showAll) return pickSetups(state.setups, toScanOptions(state.settings));
  return state.settings.onlyPerfect ? state.setups.filter((s) => s.zone?.perfect) : state.setups;
}

function resultOf(s) {
  return state.scan.results.find((x) => x.coin === s.coin);
}

/** Süre koşulu dolmadıysa limit emrin kaç mum sonra aktif olacağı. */
function barsUntilActive(s) {
  const z = s.zone;
  if (!z?.pendingTime || z.activeFrom == null) return 0;
  return Math.max(0, z.activeFrom - (resultOf(s).candles.length - 1));
}

function planSummary(s) {
  const parts = [];
  const act = activePlans(s, toScanOptions(state.settings)).map((p) => p.type);
  if (act.includes('break')) parts.push(`B kırılımı ${s.stateLabel} R:R ${fmtX(s.rr, 1)}`);
  if (act.includes('zone')) parts.push(`Bölge ${s.zone.stateLabel} R:R ${fmtX(s.zone.rr, 1)}`);
  return parts.join(' · ');
}

const REASONS = [
  [/ABC/, 'Dalga 2 ABC değil'],
  [/Süre/, "Dalga 2 dalga 1'den kısa"],
  [/geri çekilme/, 'Geri çekilme bant dışı'],
  [/İtki/, 'İtki kuralı ihlali'],
  [/Dalga 1 küçük/, 'Dalga 1 küçük'],
  [/stop/i, "Bölge stop'un ötesinde"],
];

function renderSummary() {
  const r = state.scan;
  const opts = toScanOptions(state.settings);
  if (isTri()) {
    $('#summary').innerHTML = triSummary(null, {
      results: r.results, setups: state.setups, visible: pickTriangles(state.setups, opts), tf: r.tf, at: r.at, fmtTime, minRR: opts.triMinRR,
    });
    $('#listTitle').textContent = `Üçgenler (${visibleSetups().length})`;
    return;
  }
  const errors = r.results.filter((x) => x.error);
  const vis = pickSetups(state.setups, opts);
  const nBreak = vis.filter((s) => activePlans(s, opts).some((p) => p.type === 'break')).length;
  const nZone = vis.filter((s) => activePlans(s, opts).some((p) => p.type === 'zone')).length;
  const loose = pickSetups(state.setups, { ...opts, minRR: 0 }).length;
  const counts = new Map();
  for (const d of state.diagnostics) {
    for (const reason of d.reasons) {
      const name = REASONS.find(([re]) => re.test(reason))?.[1] || reason;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
  }
  const chips = [...counts].sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span class="chip">${esc(k)}: ${v}</span>`).join('');
  const nPerfect = vis.filter((s) => s.zone?.perfect).length;
  const split = [useBreak() ? `${nBreak} B kırılımı` : '', useZone() ? `${nZone} limit bölge` : '', `★ ${nPerfect} mükemmel`].filter(Boolean).join(', ');
  $('#summary').innerHTML = `
    <p><strong>${r.results.length} coin</strong> · ${esc(r.tf.label)} · ${fmtTime(r.at)} ·
      <strong>${vis.length}</strong> aktif setup (${split})${loose > vis.length ? ` · ${loose - vis.length} tanesi R:R &lt; ${opts.minRR} olduğu için gizli` : ''} ·
      ${state.setups.length - loose} kaçan/bozulan
      ${errors.length ? `<span class="error"> · ${errors.length} coin okunamadı (${esc(errors.slice(0, 4).map((e) => e.coin).join(', '))})</span>` : ''}</p>
    ${chips ? `<div class="chips"><span class="chip muted">Elenen adaylar →</span>${chips}</div>` : ''}`;
  $('#listTitle').textContent = `Setup'lar (${visibleSetups().length})`;
}

// Dar stop: giriş geçersizlik seviyesine çok yakınsa komisyon R'nin büyük kısmını yer.
const TIGHT_PCT = 1.5;
const pctTxt = (x, d) => x.toFixed(d).replace('.', ',');
function stopCell(entry, stop, ref) {
  const pct = riskPct(entry, stop);
  return `${fmtPrice(stop, ref)} <small class="${pct < TIGHT_PCT ? 'warn-txt' : 'muted'}">%${pctTxt(pct, pct < 1 ? 2 : 1)}</small>`;
}
function tightNote(entry, stop) {
  const pct = riskPct(entry, stop);
  if (pct >= TIGHT_PCT) return '';
  const fr = feeR(entry, stop, state.settings.feePct ?? 0.1);
  return `<div class="note">⚠ Stop çok yakın (%${pctTxt(pct, 2)}): komisyon ≈ ${pctTxt(fr, 2)}R</div>`;
}

const lvBox =(label, value, cls = '') => `<div${cls ? ` class="${cls}"` : ''}><b>${esc(label)}</b><span class="num">${value}</span></div>`;

function breakBlock(s, active) {
  if (s.phase !== 'complete') {
    return `<div class="plan muted-plan"><div class="plan-head"><span class="plan-name">B kırılımı</span><span class="muted">C tamamlanınca hesaplanır · tetik B = ${fmtPrice(s.abc.B.price, s.price)}</span></div></div>`;
  }
  const L = s.levels;
  return `<div class="plan${active ? '' : ' faded'}">
    <div class="plan-head"><span class="plan-name">B kırılımı</span><span class="state ${s.state}">${esc(s.stateLabel)}</span><span class="rr">R:R <b class="num">${fmtX(s.rr, 1)}</b></span></div>
    <div class="lv">${lvBox(s.state === 'triggered' ? 'Fiyat' : 'Tetik', fmtPrice(L.entry, s.price))}${lvBox('Stop', stopCell(L.entry, L.stop, s.price))}${lvBox('Hedef 1', fmtPrice(L.targets[0].price, s.price))}${lvBox('Hedef 2', fmtPrice(L.targets[1]?.price, s.price))}</div>
    ${tightNote(L.entry, L.stop)}
  </div>`;
}

function zoneBlock(s, active) {
  const z = s.zone;
  if (!z) return `<div class="plan muted-plan"><div class="plan-head"><span class="plan-name">Limit bölge</span><span class="muted">ABC yok, bölge hesaplanamadı</span></div></div>`;
  const wait = barsUntilActive(s);
  const fill = z.legs.length > 1 && z.filledLegs ? ` (${z.filledLegs}/${z.legs.length})` : '';
  return `<div class="plan${active ? '' : ' faded'}">
    <div class="plan-head"><span class="plan-name">Limit bölge</span><span class="state zone-${z.state}">${esc(z.stateLabel)}${fill}</span>
      <span class="chip str-${z.strength}">${z.perfect ? '★ ' : ''}${esc(z.strength)}${z.inside ? ` · ${z.inside} çakışma` : ''}</span>
      <span class="rr">R:R <b class="num">${fmtX(z.rr, 1)}</b></span></div>
    ${wait ? `<div class="note">Süre koşulu: emir ${wait} mum sonra aktif</div>` : ''}
    <div class="lv">${z.perfect
      ? lvBox('Çakışma seviyesi', `${fmtPrice(z.confluence, s.price)} <small class="muted">fark ${fmtX(z.widthAtr, 2)} ATR</small>`, 'wide')
      : lvBox('Bölge', `${fmtPrice(z.near, s.price)} – ${fmtPrice(z.far, s.price)}`, 'wide')}${lvBox(z.filledLegs ? 'Ort. giriş' : 'Emir', fmtPrice(z.entry, s.price))}${lvBox('Stop', stopCell(z.entry, z.stop, s.price))}${lvBox('Hedef 2', fmtPrice(z.targets[1]?.price, s.price))}</div>
    ${tightNote(z.entry, z.stop)}
  </div>`;
}

function card(s) {
  const act = activePlans(s, toScanOptions(state.settings)).map((p) => p.type);
  const sel = state.selected && setupKey(state.selected) === setupKey(s);
  const phase = s.phase === 'forming' ? 'Dalga 2 sürüyor' : 'Dalga 2 tamam';
  const time = s.phase === 'forming'
    ? `Süre 2/1 şu an ${fmtX(s.timeRatio, 2)}${s.timeOk ? ' ✓' : ''}`
    : `Süre 2/1 = ${fmtX(s.timeRatio, 2)}`;
  return `
  <button type="button" class="card ${s.direction}${act.length ? '' : ' faded'}" data-key="${esc(setupKey(s))}" aria-current="${sel}">
    <div class="head">
      <span class="coin">${esc(s.coin)}</span>
      <span class="dir ${s.direction}">${dirTxt(s)}</span>
      <span class="chip">${phase}</span>
      <span class="score">Skor <b class="num">${s.score}</b></span>
    </div>
    <div class="meta">
      <span>Dalga 2: ${s.abc ? esc(s.abc.kind) : 'ABC yok'}</span>
      <span>Geri çekilme ${fmtPct(s.retr, 1)}${s.phase === 'forming' ? ' (şu an)' : ''}</span>
      <span>${time}</span>
      <span>derece ${s.sensitivity}×ATR</span>
    </div>
    ${useBreak() ? breakBlock(s, act.includes('break')) : ''}
    ${useZone() ? zoneBlock(s, act.includes('zone')) : ''}
  </button>`;
}

function renderCards() {
  const vis = visibleSetups();
  const box = $('#cards');
  if (!vis.length) {
    box.innerHTML = `<div class="empty muted">Bu zaman diliminde tüm koşulları sağlayan setup yok.<br/>Başka bir zaman dilimi ya da giriş tipi dene veya “Kaçan / bozulanları da göster”i aç.</div>`;
    return;
  }
  if (isTri()) {
    const sel = state.selected && setupKey(state.selected);
    box.innerHTML = vis.map((t) => triCard(t, { selected: setupKey(t) === sel, active: ['forming', 'near', 'broken'].includes(t.state) })).join('');
  } else {
    box.innerHTML = vis.map(card).join('');
  }
  for (const el of box.querySelectorAll('.card')) {
    el.addEventListener('click', () => select(vis.find((s) => setupKey(s) === el.dataset.key)));
  }
}

const ICON = { pass: '✓', warn: '⚠', fail: '✗', info: 'i', na: '–' };

function ruleList(title, results) {
  const items = results
    .filter((r) => r.status !== 'na')
    .map((r) => `<li class="${r.status}"><span class="ic" aria-label="${r.status}">${ICON[r.status] || '·'}</span>
      <div><div class="nm">${esc(r.rule.name)}</div>${r.detail ? `<div class="dt">${esc(r.detail)}</div>` : ''}<div class="src">${esc(r.rule.source || '')}</div></div></li>`)
    .join('');
  return `<h3>${esc(title)}</h3><ul>${items}</ul>`;
}

const fact = (label, value, extra = '') => `<div><b>${esc(label)}</b><span class="num">${value}</span>${extra}</div>`;

function breakFacts(s) {
  if (s.phase !== 'complete') return '';
  const L = s.levels;
  const risk = Math.abs(L.entry - L.stop) / L.entry;
  return `<h3 class="sec">B kırılımı (onaylı) · <span class="state ${s.state}">${esc(s.stateLabel)}</span></h3>
    <div class="facts">
      ${fact(s.state === 'triggered' ? 'Giriş (güncel fiyat)' : 'Tetik (B kırılımı)', fmtPrice(L.entry, s.price))}
      ${fact('Stop', fmtPrice(L.stop, s.price), ` <small class="muted">risk ${fmtPct(risk)}</small>`)}
      ${L.targets.map((t, i) => fact(`Hedef ${i + 1} (${t.k} × W1)`, fmtPrice(t.price, s.price))).join('')}
      ${fact(`R:R (hedef ${state.settings.mainTarget + 1})`, fmtX(s.rr, 2))}
    </div>`;
}

const ENTRY_TXT = { mid: 'kademeli (yarısı yakın, yarısı uzak kenar)', near: 'yakın kenar', far: 'uzak kenar', r618: 'sadece %61,8 seviyesi', c127: 'sadece C = 1,272A seviyesi' };

function zoneFacts(s) {
  const z = s.zone;
  if (!z) return '';
  const risk = Math.abs(z.entry - z.stop) / z.entry;
  const wait = barsUntilActive(s);
  const entryTxt = z.perfect ? 'tek çakışma seviyesi' : ENTRY_TXT[state.settings.zoneEntry];
  const levels = [...z.levels].sort((a, b) => b.price - a.price).map((l) =>
    `<li class="${l.edge ? 'edge' : l.inside ? 'in' : ''}"><span>${esc(l.name)}${l.edge ? ' · kenar' : ''}</span><span class="num">${fmtPrice(l.price, s.price)}</span><span>${l.inside ? '● bölgede' : ''}</span></li>`).join('');
  return `<h3 class="sec">Limit bölge (erken) · <span class="state zone-${z.state}">${esc(z.stateLabel)}</span></h3>
    ${wait ? `<p class="note">Süre koşulu: dalga 2, dalga 1'den uzun sürünce emir aktif olur (${wait} mum sonra).</p>` : ''}
    <div class="facts">
      ${fact('Yakın kenar', fmtPrice(z.near, s.price))}
      ${fact('Uzak kenar', fmtPrice(z.far, s.price))}
      ${fact('İki kenar farkı', `${fmtPrice(Math.abs(z.near - z.far), s.price)} · ${fmtX(z.widthAtr, 2)} ATR${z.perfect ? ' ★' : ''}`)}
      ${fact(z.legs.length > 1 ? 'Emir seviyeleri' : 'Emir seviyesi', z.legs.map((l) => fmtPrice(l, s.price)).join(' / '),
        ` <small class="muted">${z.front > 0 ? `seviyenin ${fmtPrice(z.front, s.price)} önünde` : 'tam seviye'} · yaklaşma uyarısı ${state.settings.approachAtr} ATR</small>`)}
      ${fact('Bölge gücü', `${esc(z.strength)} · ${fmtX(z.widthAtr, 1)} ATR · ${fmtPct(z.widthPct, 2)}`)}
      ${fact('Giriş', fmtPrice(z.entry, s.price), ` <small class="muted">${esc(entryTxt)}${z.filledLegs ? `, ${z.filledLegs}/${z.legs.length} doldu` : ''}</small>`)}
      ${fact(z.stopMode === 'r886' ? 'Stop (dalga 2 = %88,6)' : 'Stop (dalga 1 başı)', fmtPrice(z.stop, s.price), ` <small class="muted">risk ${fmtPct(risk)}</small>`)}
      ${z.targets.map((t, i) => fact(`Hedef ${i + 1} (${t.k} × W1)`, fmtPrice(t.price, s.price))).join('')}
      ${fact(`R:R (hedef ${state.settings.mainTarget + 1})`, fmtX(z.rr, 2))}
    </div>
    <ul class="levels">${levels}</ul>`;
}

function renderDetail() {
  const s = state.selected;
  const box = $('#detail');
  if (!s) {
    destroyChart();
    box.innerHTML = '<div class="empty muted">Bir setup seçince grafik ve kural kontrolleri burada görünür.</div>';
    return;
  }
  const r = resultOf(s);
  if (s.kind === 'triangle') {
    box.innerHTML = triDetail(s, { tfId: state.scan.tf.id, feePct: state.settings.feePct });
    $('#triLegend').innerHTML = TRI_LEGEND.map(([t, c, cls]) => `<span><i class="${cls}" style="border-top-color:${c}"></i>${esc(t)}</span>`).join('');
    drawTriangle($('#chart'), r.longCandles, r.live, s);
    return;
  }
  const rsi = [];
  if (s.rsi.divergence) rsi.push(`C/A uyumsuzluk ${s.rsi.divergence.ok ? 'var ✓' : 'yok'} (${s.rsi.divergence.rA.toFixed(0)} → ${s.rsi.divergence.rC.toFixed(0)})`);
  if (s.rsi.w1Momentum) rsi.push(`dalga 1 RSI ${s.rsi.w1Momentum.r1.toFixed(0)} ${s.rsi.w1Momentum.ok ? '✓' : ''}`);
  const tv = `https://www.tradingview.com/chart/?symbol=OKX%3A${encodeURIComponent(s.coin)}USDT.P`; // perp grafiği (veri perp'ten gelir)
  box.innerHTML = `
    <div class="detail-head">
      <h2>${esc(s.coin)}/USDT</h2>
      <span class="dir ${s.direction}">${dirTxt(s)}</span>
      <span class="chip">${s.phase === 'forming' ? 'Dalga 2 sürüyor' : 'Dalga 2 tamam'}</span>
      <span class="chip">${esc(state.scan.tf.id)} · derece ${s.sensitivity}×ATR</span>
      <a class="ext" href="${tv}" target="_blank" rel="noopener">TradingView'de aç ↗</a>
    </div>
    <div class="chart" id="chart"></div>
    <div class="legend">${LEGEND.filter(([, , , t]) => !t || (t === 'break' ? useBreak() : useZone())).map(([t, c, cls]) => `<span><i class="${cls}" style="border-top-color:${c}"></i>${esc(t)}</span>`).join('')}</div>
    <div class="facts">
      ${fact('Güncel fiyat', fmtPrice(s.price))}
      ${fact('Geçersizlik (0)', fmtPrice(s.points.p0.price, s.price))}
      ${fact(s.phase === 'forming' ? 'Dalga 2 geri çekilme (şu an)' : 'Dalga 2 geri çekilme', fmtPct(s.retr))}
      ${fact('Süre: dalga 1 / dalga 2', `${s.durations.D1} / ${s.durations.D2} mum (${fmtX(s.timeRatio)})${s.phase === 'forming' ? (s.timeOk ? ' ✓' : ' – henüz sağlanmadı') : ''}`)}
      ${fact('Dalga 2 yapısı', s.abc ? esc(s.abc.label) + (s.phase === 'forming' ? ' (C oluşuyor)' : '') : 'ABC bulunamadı')}
      ${s.phase === 'complete' ? fact('RSI', esc(rsi.join(' · ') || '–')) : ''}
      ${s.volume ? fact('Hacim W1 / W2', s.volume.ok ? 'W1 > W2 ✓' : 'W1 ≤ W2') : ''}
      ${fact('Skor', `${s.score}/100`)}
    </div>
    ${useBreak() ? breakFacts(s) : ''}
    ${useZone() ? zoneFacts(s) : ''}
    <div class="rules">
      ${ruleList(`İtki başlangıcı 0-1-2 (${state.settings.ruleSet})${s.phase === 'forming' ? ' — 2 = şimdiye kadarki uç' : ''}`, s.impulse.results)}
      ${s.abc ? ruleList(`Dalga 2 iç yapısı: ${s.abc.label}${s.phase === 'forming' ? ' (0-A-B, C oluşuyor)' : ''}`, s.abc.results) : ''}
    </div>`;
  drawSetup($('#chart'), r.candles, r.live, s, { showBreak: useBreak(), showZone: useZone() });
}

function select(s) {
  state.selected = s || null;
  renderCards();
  renderDetail();
  if (s && window.matchMedia('(max-width: 900px)').matches) $('#detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function render() {
  document.querySelector('.layout').classList.remove('stale');
  renderSummary();
  const vis = visibleSetups();
  const keep = state.selected && vis.find((s) => setupKey(s) === setupKey(state.selected));
  state.selected = keep || vis[0] || null;
  renderCards();
  renderDetail();
}

// ------------------------------------------------------------------ başlat
function init() {
  syncToolbar();
  renderSettings($('#settings'), state.settings, updateSettings);
  for (const b of document.querySelectorAll('#tf button')) b.addEventListener('click', () => updateSettings({ timeframe: b.dataset.tf }));
  $('#ruleSet').addEventListener('change', (e) => updateSettings({ ruleSet: e.target.value }));
  $('#entryType').addEventListener('change', (e) => updateSettings({ entryType: e.target.value }));
  $('#mode').addEventListener('change', (e) => { state.selected = null; updateSettings({ mode: e.target.value }); });
  $('#onlyPerfect').addEventListener('change', (e) => updateSettings({ onlyPerfect: e.target.checked }));
  $('#showAll').addEventListener('change', (e) => { state.settings.showAll = e.target.checked; saveSettings(state.settings); if (state.scan) render(); });
  $('#scanBtn').addEventListener('click', () => scan());
  $('#settingsBtn').addEventListener('click', () => {
    const el = $('#settings');
    el.hidden = !el.hidden;
    $('#settingsBtn').setAttribute('aria-expanded', String(!el.hidden));
  });
  setupAutoRefresh();
  scan();
}

init();

