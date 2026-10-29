const COLS_HIST = ['symbol','id','date','weekday_hk','weekday_ny','source','side','level_top','level_bot','touch_price','wo','fwd_1_high','fwd_1_low','fwd_3_high','fwd_3_low','fwd_5_high','fwd_5_low','bounce_atr','bounce_ok'];
const COLS_LEVELS = ['symbol','id','asof_date','source','side','level_top','level_bot','spot_close','confirm_date','dist_to_spot','wo','wo_id'];

let view = 'hist';
let histRows = [];
let levelRows = [];
let watchlist = { week_id: '', symbols: [], updated_at: '' };
let metaInfo = {};

const $ = (id) => document.getElementById(id);
const tbody = $('tbody');
const thead = $('thead');
const empty = $('empty');

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, '').trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length) return [];
  const headers = splitCsvLine(lines[0]).map(h => h.trim().toLowerCase());
  return lines.slice(1).map(line => {
    const cells = splitCsvLine(line);
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (cells[i] ?? '').trim(); });
    return obj;
  });
}

function splitCsvLine(line) {
  const out = [];
  let cur = '', q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (q && line[i+1] === '"') { cur += '"'; i++; }
      else q = !q;
    } else if (c === ',' && !q) { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

function isBounce(v) {
  const s = String(v ?? '').trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'y' || s === 'ok';
}

function sideLabel(side) {
  const s = String(side || '').toLowerCase();
  if (s === 'sell') return '賣方低效率';
  if (s === 'buy') return '買方低效率';
  if (s === 'sh') return 'Swing High';
  if (s === 'sl') return 'Swing Low';
  return side || '—';
}

function sourceLabel(src) {
  const s = String(src || '').toLowerCase();
  if (s === 'fvg') return '低效率';
  if (s === 'swing') return 'Swing';
  return src || '—';
}

function directionClass(side) {
  const s = String(side || '').toLowerCase();
  return (s === 'sell' || s === 'sh') ? 'down' : 'up';
}

function weekdaysFor(dateStr) {
  if (!dateStr) return { hk: '—', ny: '—' };
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return { hk: '—', ny: '—' };
  const y = +m[1], mo = +m[2], d = +m[3];
  const dow = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  const hkNames = ['星期日','星期一','星期二','星期三','星期四','星期五','星期六'];
  const nyNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  return { hk: hkNames[dow], ny: nyNames[dow] };
}

function dualDates(list) {
  const map = {};
  for (const r of list) {
    if (!isBounce(r.bounce_ok)) continue;
    const key = `${String(r.symbol || '').toUpperCase()}|${r.date}`;
    const src = String(r.source || '').toLowerCase();
    if (!map[key]) map[key] = new Set();
    map[key].add(src);
  }
  return new Set(Object.keys(map).filter(k => map[k].has('fvg') && map[k].has('swing')));
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function csvEscape(v) {
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function normalizeHist(parsed) {
  return parsed.map(r => ({
    symbol: r.symbol || 'XAUUSD',
    id: r.id || '',
    date: r.date || '',
    source: r.source || '',
    side: r.side || '',
    level_top: r.level_top || '',
    level_bot: r.level_bot || '',
    touch_price: r.touch_price || '',
    wo: r.wo || '',
    wo_id: r.wo_id || '',
    bounce_atr: r.bounce_atr || '',
    bounce_ok: r.bounce_ok || '',
    fwd_1_high: r.fwd_1_high || '',
    fwd_1_low: r.fwd_1_low || '',
    fwd_3_high: r.fwd_3_high || '',
    fwd_3_low: r.fwd_3_low || '',
    fwd_5_high: r.fwd_5_high || '',
    fwd_5_low: r.fwd_5_low || ''
  }));
}

function normalizeLevels(parsed) {
  return parsed.map(r => {
    const lvl = r.level || '';
    return {
      symbol: r.symbol || '',
      id: r.id || '',
      asof_date: r.asof_date || r.asof || r.spot_date || '',
      source: r.source || '',
      side: r.side || '',
      level_top: r.level_top || lvl,
      level_bot: r.level_bot || lvl,
      spot_close: r.spot_close || r.spot || '',
      wo: r.wo || '',
      wo_id: r.wo_id || '',
      confirm_date: r.confirm_date || '',
      dist_to_spot: r.dist_to_spot || ''
    };
  });
}

function fmtTaipei(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat('zh-HK', {
      timeZone: 'Asia/Taipei',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false
    }).format(d) + '（台北）';
  } catch {
    return iso;
  }
}

function renderWatch() {
  const chips = $('watchChips');
  const emptyEl = $('watchEmpty');
  const syms = Array.isArray(watchlist.symbols) ? watchlist.symbols : [];
  chips.innerHTML = syms.map(s => `<span class="chip">${esc(s)}</span>`).join('');
  emptyEl.classList.toggle('hidden', syms.length > 0);
  $('weekLabel').textContent = watchlist.week_id ? `本周 WO：${watchlist.week_id}` : 'week —';
  const upd = metaInfo.updated_at || watchlist.updated_at;
  $('updatedLabel').textContent = upd ? `更新：${fmtTaipei(upd)}` : '更新 —';
}

function activeRows() {
  return view === 'hist' ? histRows : levelRows;
}

function filtered() {
  const sym = $('filterSymbol').value;
  const src = $('filterSource').value;
  const side = $('filterSide').value;
  const from = $('from').value;
  const to = $('to').value;
  const list = activeRows();
  return list.filter(r => {
    if (view === 'hist' && !isBounce(r.bounce_ok)) return false;
    if (sym && String(r.symbol || '').toUpperCase() !== sym.toUpperCase()) return false;
    if (src && String(r.source || '').toLowerCase() !== src) return false;
    if (side && String(r.side || '').toLowerCase() !== side) return false;
    if (view === 'hist') {
      if (from && r.date < from) return false;
      if (to && r.date > to) return false;
    }
    return true;
  }).sort((a, b) => {
    if (view === 'hist') return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
    const sa = String(a.symbol||''), sb = String(b.symbol||'');
    if (sa !== sb) return sa < sb ? -1 : 1;
    return String(a.id||'') < String(b.id||'') ? -1 : 1;
  });
}

function setThead() {
  if (view === 'hist') {
    thead.innerHTML = `<tr>
      <th>品種</th><th>ID</th><th>日期</th><th>星期（香港）</th><th>星期（紐約）</th>
      <th>來源</th><th>方向</th><th>觸及價</th><th>反彈(ATR)</th><th>關卡</th><th>WO（參考）</th>
    </tr>`;
  } else {
    thead.innerHTML = `<tr>
      <th>品種</th><th>ID</th><th>asof</th><th>來源</th><th>方向</th>
      <th>level_top</th><th>level_bot</th><th>現價(收)</th><th>確認日</th><th>距現價</th><th>WO（參考）</th>
    </tr>`;
  }
}

function render() {
  setThead();
  renderWatch();
  const list = filtered();
  const dual = view === 'hist' ? dualDates(histRows) : new Set();
  tbody.innerHTML = '';
  $('count').textContent = view === 'hist' ? `${list.length} 日有反彈` : `${list.length} 個有效水位`;
  $('export').disabled = list.length === 0;
  $('legendHist').classList.toggle('hidden', view !== 'hist');
  $('legendLevels').classList.toggle('hidden', view !== 'levels');
  $('fromWrap').classList.toggle('hidden', view !== 'hist');
  $('toWrap').classList.toggle('hidden', view !== 'hist');

  const rows = activeRows();
  empty.classList.toggle('hidden', list.length > 0 || rows.length > 0);
  if (rows.length && list.length === 0) {
    empty.textContent = view === 'hist'
      ? '有資料，但篩選後冇符合「有反彈」嘅日子。'
      : '有資料，但篩選後冇符合嘅水位。';
    empty.classList.remove('hidden');
  } else if (!rows.length) {
    empty.textContent = '等緊 live 資料…';
  }

  for (const r of list) {
    const tr = document.createElement('tr');
    if (view === 'hist') {
      const level = r.level_top || r.level_bot || '—';
      tr.innerHTML = `
        <td>${esc(r.symbol || '—')}</td>
        <td><code>${esc(r.id || '—')}</code></td>
        <td>${esc(r.date)}${dual.has(`${String(r.symbol || '').toUpperCase()}|${r.date}`) ? ' <span class="tag dual">雙觸發</span>' : ''}</td>
        <td>${esc(weekdaysFor(r.date).hk)}</td>
        <td>${esc(weekdaysFor(r.date).ny)}</td>
        <td><span class="tag ${esc(String(r.source||'').toLowerCase())}">${esc(sourceLabel(r.source))}</span></td>
        <td><span class="tag ${directionClass(r.side)}">${esc(sideLabel(r.side))}</span></td>
        <td>${esc(r.touch_price || '—')}</td>
        <td>${esc(r.bounce_atr || '—')}</td>
        <td>${esc(level)}</td>
        <td>${esc(r.wo_id || r.wo || '—')}</td>`;
    } else {
      tr.innerHTML = `
        <td>${esc(r.symbol || '—')}</td>
        <td><code>${esc(r.id || '—')}</code></td>
        <td>${esc(r.asof_date || '—')}</td>
        <td><span class="tag ${esc(String(r.source||'').toLowerCase())}">${esc(sourceLabel(r.source))}</span></td>
        <td><span class="tag ${directionClass(r.side)}">${esc(sideLabel(r.side))}</span></td>
        <td>${esc(r.level_top || '—')}</td>
        <td>${esc(r.level_bot || '—')}</td>
        <td>${esc(r.spot_close || '—')}</td>
        <td>${esc(r.confirm_date || '—')}</td>
        <td>${esc(r.dist_to_spot || '—')}</td>
        <td>${esc(r.wo_id || r.wo || '—')}</td>`;
    }
    tbody.appendChild(tr);
  }

  const asof = metaInfo.asof ? ` · asof ${metaInfo.asof}` : '';
  $('meta').textContent = view === 'hist'
    ? (histRows.length ? `A 已載入 ${histRows.length} 行 · bounce_ok 顯示 · 取消 WO${asof}` : 'A 未載入')
    : (levelRows.length ? `B 已載入 ${levelRows.length} 行 · 相對最新收仍有效${asof}` : 'B 未載入');
}

function setView(next) {
  view = next;
  document.querySelectorAll('.tab').forEach(btn => {
    const on = btn.dataset.view === view;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  render();
}

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => setView(btn.dataset.view));
});

['filterSymbol','filterSource','filterSide','from','to'].forEach(id => $(id).addEventListener('input', render));

$('export').addEventListener('click', () => {
  const list = filtered().map(r => {
    if (view === 'hist') {
      const w = weekdaysFor(r.date);
      return { ...r, weekday_hk: w.hk, weekday_ny: w.ny };
    }
    return r;
  });
  const cols = view === 'hist' ? COLS_HIST : COLS_LEVELS;
  const header = cols.join(',');
  const body = list.map(r => cols.map(c => csvEscape(r[c] ?? '')).join(',')).join('\n');
  const blob = new Blob([header + '\n' + body], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = view === 'hist' ? 'bounce_hist_live.csv' : 'levels_vs_spot_live.csv';
  a.click();
  URL.revokeObjectURL(a.href);
});

function civilIsoHK(d = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(d);
  const get = t => parts.find(p => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

/** 今日（香港曆）對上一個星期日；若今日已係日就用今日 */
function lastSundayIso(d = new Date()) {
  const hk = civilIsoHK(d);
  const [y, mo, da] = hk.split('-').map(Number);
  const utc = new Date(Date.UTC(y, mo - 1, da, 12));
  utc.setUTCDate(utc.getUTCDate() - utc.getUTCDay());
  return utc.toISOString().slice(0, 10);
}

(() => {
  $('from').value = lastSundayIso();
  $('to').value = civilIsoHK();
})();

Promise.all([
  fetch('data_hist.csv').then(r => r.ok ? r.text() : '').catch(() => ''),
  fetch('data_levels.csv').then(r => r.ok ? r.text() : '').catch(() => ''),
  fetch('watchlist.json').then(r => r.ok ? r.json() : null).catch(() => null),
  fetch('meta.json').then(r => r.ok ? r.json() : null).catch(() => null)
]).then(([histText, levelsText, watch, meta]) => {
  if (histText && histText.trim()) histRows = normalizeHist(parseCsv(histText));
  if (levelsText && levelsText.trim()) levelRows = normalizeLevels(parseCsv(levelsText));
  if (watch) watchlist = watch;
  if (meta) metaInfo = meta;
  render();
});
