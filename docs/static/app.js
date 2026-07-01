/* ============================================================
   HZTwhipsaw — Static Site (GitHub Pages)
   All filtering/sorting/pagination client-side
   ============================================================ */

let currentPage = 1, currentSort = 'total', currentOrder = 'desc';
let currentDate = null;
let currentClass = 'all';        // 'all' | 'trend' | 'choppy'
let allStocks = [];              // currently loaded date's stocks (filtered)
let radarChart = null, historyChart = null, klineChart = null;
let dateStats = {};              // summary stats per date

const PER_PAGE = 50;

// Academic palette
const COLORS = {
  navy: '#2C3E6B', brick: '#8B3A3A', slate: '#4A6670',
  green: '#4A6B5A', gold: '#8B7A4A', warmDark: '#5C4A3A',
  bg: '#FAFAFA', line: '#D0CDC5', lineLight: '#E8E5DF',
  textDim: '#7A7671', fillRadar: 'rgba(44,62,107,0.08)',
};

function scoreClass(v) {
  if (v >= 80) return 'score-80'; if (v >= 65) return 'score-65';
  if (v >= 50) return 'score-50'; return 'score-low';
}
function pctClass(v) { return v >= 0 ? 'score-80' : 'score-low'; }
function barColor(v) {
  if (v >= 80) return COLORS.green; if (v >= 65) return COLORS.gold;
  if (v >= 50) return COLORS.slate; return COLORS.brick;
}

// ── Concept color palette ──
const CON_COLORS = {};
const CON_PALETTE = [
  '#6B7B8A','#7A8B6B','#8B7A6B','#6B8B7A','#7A6B8B','#8B6B7A',
  '#5C6E7A','#6E7A5C','#7A5C6E','#5C7A6E','#6E5C7A','#7A6E5C',
  '#4A5C6B','#5C6B4A','#6B4A5C','#4A6B5C','#5C4A6B','#6B5C4A',
  '#3D5060','#50603D','#603D50','#3D6050','#503D60','#60503D',
  '#7A8588','#857A88','#88857A','#7A8885','#857A85','#88857A',
  '#6E7378','#736E78','#78736E','#6E7873','#736E73','#78736E',
  '#5E6670','#665E70','#70665E','#5E7066','#665E6E','#6E665E',
  '#4E5860','#584E60','#60584E','#4E6058','#584E5E','#5E584E',
  '#8A7E72','#7E8A72','#8A7280','#72808A','#80728A','#728A7E',
  '#756E64','#6E7564','#75646E','#64756E','#6E6475','#756E64',
  '#635C54','#5C6354','#63545C','#54635C','#5C5463','#635C54',
  '#766E6A','#6E766A','#766A70','#6A7076','#706A76','#6A766E',
  '#5E5856','#585E56','#5E5658','#565E58','#58565E','#5E5856',
  '#4E4A48','#4A4E48','#4E484A','#484E4A',
];
let _conColorIdx = 0;
function getConceptColor(concept) {
  if (!CON_COLORS[concept]) {
    CON_COLORS[concept] = CON_PALETTE[_conColorIdx % CON_PALETTE.length];
    _conColorIdx++;
  }
  return CON_COLORS[concept];
}

function stripCode(code) {
  for (const p of ['sz.', 'sh.', 'bj.']) {
    if (code.startsWith(p)) return code.slice(p.length);
  }
  return code;
}

// ── Init ──
async function init() {
  await loadDates();
  await loadConcepts();
  await loadStorage();
  // Class toggle buttons
  document.querySelectorAll('.class-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      document.querySelectorAll('.class-btn').forEach(b => b.classList.remove('active'));
      this.classList.add('active');
      currentClass = this.dataset.class;
      currentPage = 1;
      renderTable();
      // Update header stats
      if (allStocks.length > 0) updateStats({ date: currentDate, total: allStocks.length });
    });
  });

  // ── Mobile: sidebar drawer ──
  const menuBtn = document.getElementById('menuBtn');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const closeBtn = document.getElementById('sidebarCloseBtn');

  function openSidebar() {
    sidebar.classList.add('open');
    overlay.classList.add('show');
    document.body.style.overflow = 'hidden';
  }
  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
    document.body.style.overflow = '';
  }
  if (menuBtn) menuBtn.addEventListener('click', openSidebar);
  if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
  if (overlay) overlay.addEventListener('click', closeSidebar);

  // Close sidebar when a select filter changes (user made a choice)
  sidebar.querySelectorAll('select').forEach(el => {
    el.addEventListener('change', () => {
      if (window.innerWidth <= 768) closeSidebar();
    });
  });

  await loadData();
}
init();

// ── Load available dates ──
async function loadDates() {
  try {
    const r = await fetch('data/dates.json');
    const d = await r.json();
    dateStats = d.stats || {};
    const sel = document.getElementById('dateSelect');
    const qualityTag = { full: '[完]', partial: '[部]', sparse: '[稀]', unknown: '[?]' };
    sel.innerHTML = d.dates.map(dt => {
      const q = (dateStats[dt] && dateStats[dt].quality) || 'unknown';
      const sc = (dateStats[dt] && dateStats[dt].stock_count) || (dateStats[dt] && dateStats[dt].cnt) || 0;
      return `<option value="${dt}">${qualityTag[q]} ${dt} · ${sc}只</option>`;
    }).join('');
    if (d.latest) { sel.value = d.latest; currentDate = d.latest; }
    sel.addEventListener('change', () => {
      currentDate = sel.value;
      loadData();
    });
  } catch(e) { console.error('Failed to load dates', e); }
}

// ── Load concepts ──
async function loadConcepts() {
  try {
    const r = await fetch('data/concepts.json');
    const d = await r.json();
    const sel = document.getElementById('conceptSelect');
    sel.innerHTML = '<option value="all">全部概念</option>' +
      d.concepts.map(c => `<option value="${c}">${c}</option>`).join('');
  } catch(e) { console.error('Failed to load industries', e); }
}

// ── Load storage monitor ──
async function loadStorage() {
  try {
    const r = await fetch('data/storage.json');
    const d = await r.json();
    renderStorageBar('storageBarRepo', 'storageValRepo', d.repo_mb, d.repo_mb, '仓库');
    renderStorageBar('storageBarCache', 'storageValCache', d.cache_mb, d.cache_limit_mb, '缓存');
    renderStorageBar('storageBarPages', 'storageValPages', d.pages_mb, d.pages_mb, '页面');
    document.getElementById('storageUpdated').textContent = '更新于 ' + d.updated;
  } catch(e) {
    document.getElementById('storageUpdated').textContent = '储存数据加载失败';
  }
}

function renderStorageBar(barId, valId, usedMb, limitMb, label) {
  const bar = document.getElementById(barId);
  const val = document.getElementById(valId);
  if (!bar || !val) return;
  const pct = Math.min(100, (usedMb / Math.max(limitMb, 1)) * 100);
  bar.style.width = pct + '%';
  const usedStr = usedMb >= 1024 ? (usedMb / 1024).toFixed(1) + ' GB' : usedMb.toFixed(0) + ' MB';
  const limitStr = limitMb >= 1024 ? (limitMb / 1024).toFixed(0) + ' GB' : limitMb.toFixed(0) + ' MB';
  val.textContent = usedStr + ' / ' + limitStr;
  // Color warning if >80%
  if (pct > 80) {
    bar.style.background = 'var(--brick)';
  }
}

// ── Load stock data for selected date ──
async function loadData() {
  if (!currentDate) return;
  document.getElementById('tableBody').innerHTML = '<tr><td colspan="99" class="loading">加载中...</td></tr>';

  try {
    const r = await fetch(`data/${currentDate}.json`);
    const d = await r.json();
    allStocks = d.stocks || [];
    updateStats(d);
    currentPage = 1;
    renderTable();
  } catch(e) {
    document.getElementById('tableBody').innerHTML = '<tr><td colspan="99" class="loading">数据加载失败</td></tr>';
  }
}

// ── Header stats ──
function updateStats(d) {
  document.getElementById('statDate').textContent = d.date || currentDate;
  const ds = dateStats[currentDate];
  // Use filtered stocks for stats
  const filtered = currentClass === 'all' ? allStocks : allStocks.filter(s => s.trend_class === currentClass);
  document.getElementById('statTotal').textContent = filtered.length;
  const totals = filtered.map(s => s.total);
  const avg = totals.length > 0 ? (totals.reduce((a,b) => a+b, 0) / totals.length).toFixed(1) : '--';
  const max = totals.length > 0 ? Math.max(...totals).toFixed(1) : '--';
  document.getElementById('statAvg').textContent = avg;
  document.getElementById('statMax').textContent = max;
  document.getElementById('statLimit').textContent = filtered.filter(s => s.is_limit_up_today).length;
  // 趋势/震荡计数
  const trendCnt = allStocks.filter(s => s.trend_class === 'trend').length;
  const choppyCnt = allStocks.filter(s => s.trend_class !== 'trend').length;
  document.getElementById('trendCnt').textContent = trendCnt;
  document.getElementById('choppyCnt').textContent = choppyCnt;
  // 数据质量
  const qlabel = { full: '完整', partial: '部分', sparse: '稀疏', unknown: '未知' };
  document.getElementById('statQuality').textContent = ds ? (qlabel[ds.quality] || '?') : '?';
  document.getElementById('statRaw').textContent = ds ? (ds.stock_count || ds.cnt || '?') : '?';
}

// ── Filter and sort ──
function getFiltered() {
  let stocks = [...allStocks];

  // Class filter (趋势/震荡)
  if (currentClass !== 'all') {
    stocks = stocks.filter(s => s.trend_class === currentClass);
  }

  // Search
  const search = document.getElementById('searchInput').value.trim().toLowerCase();
  if (search) {
    stocks = stocks.filter(s => s.code.toLowerCase().includes(search));
  }

  // Board
  const board = document.getElementById('boardSelect').value;
  if (board === 'main') stocks = stocks.filter(s => /sh\.60|sz\.00/.test(s.code));
  else if (board === 'star') stocks = stocks.filter(s => /sh\.68/.test(s.code));
  else if (board === 'chi') stocks = stocks.filter(s => /sz\.30/.test(s.code));
  else if (board === 'bj') stocks = stocks.filter(s => /bj\./.test(s.code));

  // Concept
  const concept = document.getElementById('conceptSelect').value;
  if (concept && concept !== 'all') {
    stocks = stocks.filter(s => s.concept === concept);
  }

  // Exclude limit-up
  if (document.getElementById('limitFilter').value === '1') {
    stocks = stocks.filter(s => !s.is_limit_up_today);
  }

  // Min score
  const minScore = parseFloat(document.getElementById('minScore').value) || 0;
  stocks = stocks.filter(s => s.total >= minScore);

  // Sort
  const asc = currentOrder === 'asc';
  stocks.sort((a, b) => {
    const va = a[currentSort] || 0;
    const vb = b[currentSort] || 0;
    return asc ? va - vb : vb - va;
  });

  return stocks;
}

// ── Render table ──
function renderTable() {
  const filtered = getFiltered();
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));
  if (currentPage > totalPages) currentPage = totalPages;
  const start = (currentPage - 1) * PER_PAGE;
  const pageStocks = filtered.slice(start, start + PER_PAGE);

  const tbody = document.getElementById('tableBody');
  if (pageStocks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="99" class="loading">无匹配结果</td></tr>';
    document.getElementById('pagination').innerHTML = '';
    return;
  }

  tbody.innerHTML = pageStocks.map((s, i) => `
    <tr onclick="selectStock('${s.code}')" data-code="${s.code}">
      <td class="col-rank">${start + i + 1}</td>
      <td class="col-code">${s.code}</td>
      <td class="col-name">${s.name}</td>
      <td class="col-class"><span class="class-tag ${s.trend_class || 'choppy'}">${s.trend_class === 'trend' ? '趋势' : '震荡'}</span></td>
      <td class="col-score ${scoreClass(s.total)}">${s.total.toFixed(1)}</td>
      <td class="col-dim ${scoreClass(s.stock_strength||0)}">${(s.stock_strength||0).toFixed(0)}</td>
      <td class="col-dim ${scoreClass(s.washout_quality)}">${s.washout_quality}</td>
      <td class="col-dim ${scoreClass(s.probe_test)}">${s.probe_test}</td>
      <td class="col-dim ${scoreClass(s.ma_convergence)}">${s.ma_convergence}</td>
      <td class="col-dim ${scoreClass(s.launch_readiness)}">${s.launch_readiness}</td>
      <td class="col-dim ${scoreClass(s.volume_price_health||0)}">${(s.volume_price_health||0).toFixed(0)}</td>
      <td class="col-probe">${s.probe_count}</td>
      <td class="col-concept"><span style="color:${getConceptColor(s.concept)};font-weight:600;white-space:nowrap;">${s.concept}</span></td>
      <td class="col-price">${s.latest_close.toFixed(2)}</td>
      <td class="col-pct ${pctClass(s.latest_pctChg)}">${s.latest_pctChg >= 0 ? '+' : ''}${s.latest_pctChg.toFixed(2)}%</td>
    </tr>
  `).join('');

  // Pagination
  const pg = document.getElementById('pagination');
  let html = `<button ${currentPage <= 1 ? 'disabled' : ''} onclick="goPage(${currentPage-1})">上一页</button>`;
  const sp = Math.max(1, currentPage - 2);
  const ep = Math.min(totalPages, currentPage + 2);
  for (let p = sp; p <= ep; p++) {
    html += `<button class="${p === currentPage ? 'active' : ''}" onclick="goPage(${p})">${p}</button>`;
  }
  html += `<button ${currentPage >= totalPages ? 'disabled' : ''} onclick="goPage(${currentPage+1})">下一页</button>`;
  html += `<span class="page-info">共 ${total} 只</span>`;
  pg.innerHTML = html;
}

function goPage(p) { currentPage = p; renderTable(); }

// ── Column sort ──
document.getElementById('stockTable').addEventListener('click', function(e) {
  const th = e.target.closest('th.sortable');
  if (!th) return;
  const sk = th.dataset.sort;
  if (currentSort === sk) { currentOrder = currentOrder === 'desc' ? 'asc' : 'desc'; }
  else { currentSort = sk; currentOrder = 'desc'; }
  document.querySelectorAll('th.sortable').forEach(h => h.classList.remove('active'));
  th.classList.add('active');
  currentPage = 1;
  renderTable();
});

// ── Filter change handlers ──
['searchInput', 'boardSelect', 'conceptSelect', 'limitFilter'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(id === 'searchInput' ? 'input' : 'change', () => { currentPage = 1; renderTable(); });
});
document.getElementById('minScore').addEventListener('input', function() {
  document.getElementById('minScoreLabel').innerHTML = '&ge; ' + this.value;
  currentPage = 1;
  renderTable();
});
document.getElementById('sortBy').addEventListener('change', function() {
  currentSort = this.value; currentPage = 1; renderTable();
});
document.getElementById('sortOrder').addEventListener('change', function() {
  currentOrder = this.value; currentPage = 1; renderTable();
});

// ── Stock detail ──
async function selectStock(code) {
  document.querySelectorAll('#stockTable tbody tr').forEach(r => r.classList.remove('selected'));
  const row = document.querySelector(`tr[data-code="${code}"]`);
  if (row) row.classList.add('selected');

  // Find stock in current data
  const stock = allStocks.find(s => s.code === code);
  if (!stock) return;

  // Fetch history
  let historyData = null;
  try {
    const hr = await fetch(`data/history/${stripCode(code)}.json`);
    if (hr.ok) historyData = await hr.json();
  } catch(e) { /* no history */ }

  renderDetail(stock, historyData);

  // Mobile: slide in detail panel
  if (window.innerWidth <= 768) {
    const panel = document.getElementById('detailPanel');
    panel.classList.add('open');
    document.body.style.overflow = 'hidden';
    // Resize charts after slide-in animation completes (250ms)
    setTimeout(() => {
      if (radarChart) radarChart.resize();
      if (historyChart) historyChart.resize();
      if (klineChart) klineChart.resize();
    }, 300);
  }
}

function closeDetailMobile() {
  document.getElementById('detailPanel').classList.remove('open');
  document.body.style.overflow = '';
}

function renderDetail(stock, hist) {
  const panel = document.getElementById('detailPanel');
  const isMobile = window.innerWidth <= 768;
  panel.innerHTML = `
    ${isMobile ? '<button class="detail-back-btn" id="detailBackBtn">&larr; 返回列表</button>' : ''}
    <div class="detail-header">
      <div>
        <div class="name">${stock.name} <span class="class-tag ${stock.trend_class || 'choppy'}">${stock.trend_class === 'trend' ? '趋势' : '震荡'}</span></div>
        <div class="code">${stock.code}</div>
      </div>
      <div class="total-score ${scoreClass(stock.total)}">${stock.total.toFixed(1)}</div>
    </div>
    <div class="detail-price-row">
      <span>收盘 <strong>${stock.latest_close.toFixed(2)}</strong></span>
      <span>涨跌 <strong class="${pctClass(stock.latest_pctChg)}">${stock.latest_pctChg >= 0 ? '+' : ''}${stock.latest_pctChg.toFixed(2)}%</strong></span>
      <span>概念 <strong>${stock.concept}</strong></span>
    </div>

    <div class="radar-box" id="radarChart"></div>

    <div class="score-bars">
      ${['stock_strength','washout_quality','probe_test','ma_convergence','launch_readiness','volume_price_health']
        .map(k => {
          const labels = { stock_strength:'股票强度', washout_quality:'洗盘质量', probe_test:'试盘信号', ma_convergence:'均线粘合',
                          launch_readiness:'启动准备', volume_price_health:'量价健康' };
          const v = stock[k] || 0;
          return `
          <div class="score-bar-row">
            <span class="score-bar-label">${labels[k]}</span>
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${v}%;background:${barColor(v)}"></div></div>
            <span class="score-bar-val ${scoreClass(v)}">${v.toFixed(0)}</span>
          </div>`;
        }).join('')}
    </div>

    <div class="history-box" id="historyChart"></div>

    <div class="kline-box" id="klineChart"></div>

    <div class="detail-stats">
      <div class="detail-stat">
        <div class="val">${stock.recent_limit_days}</div>
        <div class="lbl">近5日涨停</div>
      </div>
      <div class="detail-stat">
        <div class="val" style="color:${stock.is_limit_up_today ? COLORS.brick : COLORS.green}">${stock.is_limit_up_today ? '是' : '否'}</div>
        <div class="lbl">今日涨停</div>
      </div>
      <div class="detail-stat">
        <div class="val">${stock.probe_count}</div>
        <div class="lbl">试盘次数</div>
      </div>
      <div class="detail-stat">
        <div class="val">${stock.days_since_probe >= 99 ? '&mdash;' : stock.days_since_probe + '天前'}</div>
        <div class="lbl">最近试盘</div>
      </div>
      <div class="detail-stat">
        <div class="val">${stock.concept !== '—' ? stock.concept : '—'}</div>
        <div class="lbl">所属概念</div>
      </div>
      <div class="detail-stat">
        <div class="val">${stock.rank}</div>
        <div class="lbl">当日排名</div>
      </div>
    </div>
  `;

  // Mobile: back button listener
  const backBtn = document.getElementById('detailBackBtn');
  if (backBtn) backBtn.addEventListener('click', closeDetailMobile);

  setTimeout(() => renderRadar(stock), 50);
  setTimeout(() => renderHistory(hist), 80);
  setTimeout(() => renderKline(hist), 110);
}

// ── Radar chart ──
function renderRadar(stock) {
  const dom = document.getElementById('radarChart');
  if (!dom) return;
  if (radarChart) radarChart.dispose();
  radarChart = echarts.init(dom);

  const labels = ['强度', '洗盘', '试盘', '均粘', '启动', '量价'];
  const keys = ['stock_strength', 'washout_quality', 'probe_test', 'ma_convergence', 'launch_readiness', 'volume_price_health'];
  const values = keys.map(k => stock[k] || 0);

  radarChart.setOption({
    tooltip: { trigger: 'item', backgroundColor: '#FFF', borderColor: '#D0CDC5', textStyle: { color: '#2B2B2B', fontSize: 12 } },
    radar: {
      center: ['50%', '52%'], radius: '68%',
      indicator: labels.map(l => ({ name: l, max: 100 })),
      axisName: { fontSize: 10, color: '#7A7671', fontFamily: 'Times New Roman, serif', fontStyle: 'italic' },
      splitArea: { areaStyle: { color: ['rgba(44,62,107,0.02)', 'rgba(44,62,107,0.02)'] } },
      splitLine: { lineStyle: { color: '#E0DCD5', type: 'dashed' } },
      axisLine: { lineStyle: { color: '#D0CDC5' } },
    },
    series: [{
      type: 'radar',
      data: [{ value: values, name: stock.name,
        areaStyle: { color: COLORS.fillRadar },
        lineStyle: { color: COLORS.navy, width: 1.2 },
        itemStyle: { color: COLORS.navy },
      }],
      symbol: 'circle', symbolSize: 4,
    }],
  });
}

// ── History chart ──
function renderHistory(hist) {
  const dom = document.getElementById('historyChart');
  if (!dom) return;
  if (historyChart) historyChart.dispose();
  historyChart = echarts.init(dom);

  if (!hist || !hist.history || hist.history.length < 2) {
    historyChart.setOption({
      title: { text: '历史数据不足', left: 'center', top: 'center',
        textStyle: { fontSize: 12, color: '#B0ACA7', fontFamily: 'Times New Roman, serif', fontStyle: 'italic' } }
    });
    return;
  }

  const dates = hist.history.map(r => r.date);
  const totals = hist.history.map(r => r.total);
  const ranks = hist.history.map(r => r.rank);

  historyChart.setOption({
    tooltip: {
      trigger: 'axis', backgroundColor: '#FFF', borderColor: '#D0CDC5',
      textStyle: { color: '#2B2B2B', fontSize: 11, fontFamily: 'Times New Roman, serif' },
      formatter: function(params) {
        const idx = params[0].dataIndex;
        return `${params[0].axisValue}<br/>总分: ${totals[idx].toFixed(1)}<br/>排名: #${ranks[idx]}`;
      }
    },
    legend: {
      data: ['总分', '排名'], right: 8, top: 2,
      textStyle: { fontSize: 10, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
      itemWidth: 16, itemHeight: 1,
    },
    grid: { left: 42, right: 48, top: 28, bottom: 28 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { fontSize: 8, color: '#7A7671', fontFamily: 'Times New Roman, serif', rotate: 30 },
      axisLine: { lineStyle: { color: '#D0CDC5' } }, axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '总分', min: function(v) { return Math.floor(v.min / 10) * 10; }, max: 100,
        axisLabel: { fontSize: 9, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
        splitLine: { lineStyle: { color: '#E8E5DF', type: 'dashed' } },
        axisLine: { show: false },
        nameTextStyle: { fontSize: 9, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
      },
      {
        type: 'value', name: '排名', inverse: true, min: 1,
        axisLabel: { fontSize: 9, color: '#7A7671', fontFamily: 'Times New Roman, serif',
          formatter: function(v) { return '#' + v; } },
        splitLine: { show: false }, axisLine: { show: false },
        nameTextStyle: { fontSize: 9, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
      },
    ],
    series: [
      {
        name: '总分', type: 'line', data: totals, yAxisIndex: 0,
        lineStyle: { color: COLORS.navy, width: 1.5 },
        itemStyle: { color: COLORS.navy }, symbol: 'circle', symbolSize: 4,
      },
      {
        name: '排名', type: 'line', data: ranks, yAxisIndex: 1,
        lineStyle: { color: COLORS.brick, width: 1, type: 'dashed' },
        itemStyle: { color: COLORS.brick }, symbol: 'diamond', symbolSize: 4,
      },
    ],
  });
}

// ── K-line chart ──
function renderKline(hist) {
  const dom = document.getElementById('klineChart');
  if (!dom) return;
  if (klineChart) klineChart.dispose();
  klineChart = echarts.init(dom);

  if (!hist || !hist.kline || hist.kline.length < 3) {
    klineChart.setOption({
      title: { text: 'K线数据加载中...', left: 'center', top: 'center',
        textStyle: { fontSize: 12, color: '#B0ACA7', fontFamily: 'Times New Roman, serif', fontStyle: 'italic' } }
    });
    return;
  }

  const kline = hist.kline;
  const dates = kline.map(r => r.date);
  const ohlc = kline.map(r => [r.open, r.close, r.low, r.high]);
  const volumes = kline.map(r => r.volume);

  klineChart.setOption({
    grid: [
      { left: 55, right: 12, top: 8, height: '60%' },
      { left: 55, right: 12, top: '76%', height: '20%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#D0CDC5' } }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1,
        axisLabel: { fontSize: 8, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
        axisLine: { lineStyle: { color: '#D0CDC5' } }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true,
        splitLine: { lineStyle: { color: '#E8E5DF', type: 'dashed' } },
        axisLabel: { fontSize: 9, color: '#7A7671', fontFamily: 'Times New Roman, serif' },
        axisLine: { show: false } },
      { type: 'value', gridIndex: 1, axisLabel: { show: false },
        splitLine: { show: false }, axisLine: { show: false } },
    ],
    series: [
      {
        type: 'candlestick', name: 'Price', data: ohlc,
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: COLORS.brick, color0: COLORS.green,
          borderColor: COLORS.brick, borderColor0: COLORS.green },
      },
      {
        type: 'bar', name: 'Volume',
        data: volumes.map((v, i) => {
          const isUp = ohlc[i][1] >= ohlc[i][0];
          return { value: v,
            itemStyle: { color: isUp ? 'rgba(139,58,58,0.35)' : 'rgba(74,107,90,0.35)' } };
        }),
        xAxisIndex: 1, yAxisIndex: 1,
      },
    ],
  });
}

// ── Resize ──
window.addEventListener('resize', () => {
  if (radarChart) radarChart.resize();
  if (historyChart) historyChart.resize();
  if (klineChart) klineChart.resize();
});

// ── Keyboard nav ──
document.addEventListener('keydown', function(e) {
  if (e.key === 'ArrowLeft' && currentPage > 1) { currentPage--; renderTable(); }
  if (e.key === 'ArrowRight') { currentPage++; renderTable(); }
});
