// Üçgen kırılımı modu: kart, özet ve ayrıntı HTML'i (app.js çağırır; grafik chart.js drawTriangle).
import { fmtPrice, fmtPct, fmtX, esc } from './format.js';
import { riskPct, feeR } from '../scan/wave3.js';

const dirTxt = (t) => (t.d === 1 ? 'Yukarı kırılım ▲' : 'Aşağı kırılım ▼');
const TYPE_ICON = { asc: '◢', desc: '◥' };

export const triKey = (t) => `${t.coin}|tri|${t.type}|${t.first.price}|${t.level}`;

const lv = (label, value, cls = '') => `<div${cls ? ` class="${cls}"` : ''}><b>${esc(label)}</b><span class="num">${value}</span></div>`;

function stopTxt(t) {
  const pct = riskPct(t.levels.entry, t.levels.stop);
  return `${fmtPrice(t.levels.stop, t.price)} <small class="${pct < 1.5 ? 'warn-txt' : 'muted'}">%${pct.toFixed(pct < 1 ? 2 : 1).replace('.', ',')}</small>`;
}

export function triCard(t, { selected, active }) {
  const L = t.levels;
  const fib = L.targets.find((x) => x.key === 'f618');
  return `
  <button type="button" class="card ${t.direction}${active ? '' : ' faded'}" data-key="${esc(triKey(t))}" aria-current="${selected}">
    <div class="head">
      <span class="coin">${esc(t.coin)}</span>
      <span class="dir ${t.direction}">${TYPE_ICON[t.type]} ${esc(t.typeLabel)}</span>
      <span class="state tri-${t.state}">${esc(t.stateLabel)}</span>
      <span class="score">Skor <b class="num">${t.score}</b> · R:R <b class="num">${fmtX(t.rr, 1)}</b></span>
    </div>
    <div class="meta">
      <span>${esc(t.contextLabel)}</span>
      <span>${t.touches.flat} dokunuş düz kenar · ${t.touches.slope} eğik kenar</span>
      <span>${t.widthBars} mum</span>
      <span>derece ${t.sensitivity}×ATR</span>
    </div>
    <div class="lv">
      ${lv(t.state === 'broken' ? 'Fiyat' : 'Kırılım', fmtPrice(L.entry, t.price))}
      ${lv('Stop', stopTxt(t))}
      ${lv('Ölçülü hedef', fmtPrice(L.targets[0].price, t.price))}
      ${lv(fib ? (t.fibMode === 'retr' ? '%61,8 geri' : '%61,8 izdüşüm') : 'Ana hedef', fmtPrice((fib || L.main).price, t.price))}
    </div>
  </button>`;
}

export function triSummary(t, { results, setups, visible, tf, at, fmtTime, minRR }) {
  const errors = results.filter((x) => x.error);
  const vis = (st) => visible.filter((x) => x.state === st).length;
  const all = (st) => setups.filter((x) => x.state === st).length;
  const active = setups.filter((x) => ['forming', 'near', 'broken'].includes(x.state)).length;
  return `<p><strong>${results.length} coin</strong> · ${esc(tf.label)} · ${fmtTime(at)} ·
    <strong>${visible.length}</strong> üçgen (${vis('broken')} kırıldı, ${vis('near')} kırılıma yakın, ${vis('forming')} oluşuyor)
    ${active > visible.length ? ` · ${active - visible.length} tanesi R:R &lt; ${minRR} olduğu için gizli` : ''} ·
    ${all('failed')} bozuldu · ${all('old')} eski kırılım
    ${errors.length ? `<span class="error"> · ${errors.length} coin okunamadı</span>` : ''}</p>`;
}

const fact = (label, value, extra = '') => `<div><b>${esc(label)}</b><span class="num">${value}</span>${extra}</div>`;

export function triDetail(t, { tfId, feePct }) {
  const L = t.levels;
  const pct = riskPct(L.entry, L.stop);
  const fr = feeR(L.entry, L.stop, feePct ?? 0.1);
  const tv = `https://www.tradingview.com/chart/?symbol=OKX%3A${encodeURIComponent(t.coin)}USDT.P`; // perp grafiği (veri perp'ten gelir)
  const targets = L.targets.map((x) => {
    const beyond = t.d * (x.price - L.entry) > 0;
    const main = x.key === L.main.key;
    return `<li class="${main ? 'edge' : beyond ? 'in' : ''}"><span>${esc(x.name)}${main ? ' · ana hedef' : ''}</span><span class="num">${fmtPrice(x.price, t.price)}</span><span>${beyond ? fmtPct(Math.abs(x.price - L.entry) / L.entry, 1) : 'geride'}</span></li>`;
  }).join('');
  const prior = t.prior
    ? `${fmtPrice(t.prior.from.price, t.price)} → ${fmtPrice(t.prior.to.price, t.price)} (${t.prior.dir === 'down' ? 'düşüş' : 'yükseliş'} %${(Math.abs(t.prior.from.price - t.prior.to.price) / t.prior.from.price * 100).toFixed(1).replace('.', ',')})`
    : 'yok (önceki hareket kalıp yüksekliğinin 2 katından küçük)';
  return `
    <div class="detail-head">
      <h2>${esc(t.coin)}/USDT</h2>
      <span class="dir ${t.direction}">${TYPE_ICON[t.type]} ${esc(t.typeLabel)}</span>
      <span class="state tri-${t.state}">${esc(t.stateLabel)}</span>
      <span class="chip">${esc(tfId)} · derece ${t.sensitivity}×ATR</span>
      <a class="ext" href="${tv}" target="_blank" rel="noopener">TradingView'de aç ↗</a>
    </div>
    <div class="chart" id="chart"></div>
    <div class="legend" id="triLegend"></div>
    <div class="facts">
      ${fact('Güncel fiyat', fmtPrice(t.price))}
      ${fact('Yön', dirTxt(t))}
      ${fact(t.type === 'asc' ? 'Direnç (düz tepeler)' : 'Destek (düz dipler)', fmtPrice(t.level, t.price))}
      ${fact('Kırılım seviyesi', fmtPrice(L.breakLevel, t.price), ' <small class="muted">kapanış bu seviyenin ötesinde</small>')}
      ${fact(t.state === 'broken' ? 'Giriş (güncel fiyat)' : 'Giriş', fmtPrice(L.entry, t.price))}
      ${fact('Stop (son ' + (t.type === 'asc' ? 'dibin' : 'tepenin') + ' ötesi)', fmtPrice(L.stop, t.price), ` <small class="${pct < 1.5 ? 'warn-txt' : 'muted'}">risk %${pct.toFixed(2).replace('.', ',')} · komisyon ≈ ${fr.toFixed(2).replace('.', ',')}R</small>`)}
      ${fact('Üçgen yüksekliği', fmtPrice(t.height, t.price), ` <small class="muted">${fmtX(t.height / L.atr, 1)} ATR</small>`)}
      ${fact('Genişlik', `${t.widthBars} mum`)}
      ${fact('Dokunuş', `${t.touches.flat} düz · ${t.touches.slope} eğik`)}
      ${fact('Bağlam', esc(t.contextLabel))}
      ${fact('Önceki hareket', prior)}
      ${fact(`R:R (${esc(L.main.name)})`, fmtX(t.rr, 2))}
    </div>
    <h3 class="sec">Hedefler ${t.fibMode === 'retr' ? '· önceki hareketin geri çekilmeleri' : t.fibMode === 'proj' ? '· kırılımdan izdüşüm' : ''}</h3>
    <ul class="levels">${targets}</ul>
    <p class="muted small">Kırılımın geçmişte genelde nereye kadar gittiğini Backtest sayfasındaki "Nereye kadar gitti" tablosu gösterir.</p>`;
}
