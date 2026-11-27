// app.js —— 静态3页版看板（直接读取 ./data/snapshot.json，零后端依赖）
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const state = { page: 'dash', week: 0, tenor: '10Y国债', tenorBroker: '', broker: '' };
  let periods; let S = null;      // snapshot.json
  let TMAP = {};     // tenor id -> meta
  let VMAP = {};     // view id -> view

  // ---------- 格式化与配色 ----------
  const fy = (type, v) => v == null ? '—' : (type === 'spread' ? Number(v).toFixed(0) + 'bp' : Number(v).toFixed(2) + '%');
  const sgn = (x, d = 1) => x == null ? '—' : (x > 0 ? '+' : '') + Number(x).toFixed(d);
  const tgtTxt = v => {
    const tp = (TMAP[v.tenor] || {}).type;
    if (v.target_low != null && v.target_high != null) {
      return tp === 'spread' ? `${Number(v.target_low).toFixed(0)}–${Number(v.target_high).toFixed(0)}bp`
        : `${Number(v.target_low).toFixed(2)}–${Number(v.target_high).toFixed(2)}%`;
    }
    return v.target_note || '未给点位';
  };
  const DIRC = { '2': '#c92a2a', '1': '#ff6b6b', '0': '#adb5bd', '-1': '#51cf66', '-2': '#2b8a3e' };
  const dirColor = d => DIRC[String(d)] || '#adb5bd';
  function consColor(c) {
    if (c == null) return '#ced4da';
    const x = Math.max(-2, Math.min(2, c)) / 2;
    const n = [173, 181, 189], tg = x >= 0 ? [201, 42, 42] : [43, 138, 62], k = Math.abs(x);
    return `rgb(${n.map((v, i) => Math.round(v + (tg[i] - v) * k)).join(',')})`;
  }
  const DIRL = { '2': '强烈看多', '1': '偏多', '0': '中性', '-1': '偏空', '-2': '强烈看空' };
  const SPL = { '2': '强烈看平', '1': '偏平', '0': '中性', '-1': '偏陡', '-2': '强烈看陡' };
  const dirLabel = (d, type) => (type === 'spread' ? SPL : DIRL)[String(d)] || '中性';
  function consLabel(c, type) {
    if (c == null) return '无覆盖';
    const sp = type === 'spread';
    if (c >= 1.2) return sp ? '明确看平' : '明确偏多';
    if (c >= .4) return sp ? '偏平' : '偏多';
    if (c > -.4) return '中性';
    if (c > -1.2) return sp ? '偏陡' : '偏空';
    return sp ? '明确看陡' : '明确偏空';
  }
  const deltaBadge = v => {
    if (!v.vote_eligible) return '<span class="stale">仅展示·不计单期限票</span>';
    const gap = v.gap_weeks && v.gap_weeks > 1 ? `(${v.gap_weeks}周前)` : '';
    if (v.delta === '更偏多') return `<span class="db u">▲ 更偏多${gap}</span>`;
    if (v.delta === '更偏空') return `<span class="db d">▼ 更偏空${gap}</span>`;
    if (v.delta === '维持') return `<span class="db k">维持${gap}</span>`;
    return '<span class="db n">新覆盖</span>';
  };
  const driverChip = (dr, showLayer = false) => `<span class="chip ${dr.impact === '利多' ? 'p' : dr.impact === '利空' ? 'm' : ''}" title="${dr.layer} · ${dr.impact}">${showLayer === true ? `[${dr.layer}] ` : ''}${dr.phrase}</span>`;
  const lvl = (x, a, b, labs) =>
    x == null ? '<span class="lv na">样本不足</span>' :
    x < a ? `<span class="lv lo">${labs[0]}</span>` :
    x < b ? `<span class="lv mid">${labs[1]}</span>` :
    `<span class="lv hi">${labs[2]}</span>`;

  // ---------- 图表管理 ----------
  let charts = [];
  function mk(id, opt) {
    const el = document.getElementById(id);
    if (!el) return null;
    const c = echarts.init(el);
    c.setOption({animation:false, ...opt});
    charts.push(c);
    return c;
  }
  function clearCharts() { charts.forEach(c => c.dispose()); charts = []; }
  window.addEventListener('resize', () => charts.forEach(c => c.resize()));
  const AX = { axisLine: { lineStyle: { color: '#bcccdc' } }, axisLabel: { color: '#627d98' }, splitLine: { lineStyle: { color: '#eef2f6' } } };

  // ---------- 小组件 ----------
  function consCell(c, type) {
    if (c == null) return '<span class="dF">无覆盖</span>';
    const pct = Math.min(1, Math.abs(c) / 2) * 50;
    const pos = c >= 0 ? `left:50%;width:${pct}%` : `left:${50 - pct}%;width:${pct}%`;
    return `<div class="cons"><span style="color:${consColor(c)}">${sgn(c, 2)}</span><div class="cbar"><i style="${pos};background:${consColor(c)}"></i></div><small>${consLabel(c, type)}</small></div>`;
  }
  const stackBar = m => `<div class="sbw" title="偏多 ${m.bull} · 中性 ${m.neu} · 偏空 ${m.bear}"><div class="sb"><i class="b" style="flex:${m.bull}"></i><i class="n" style="flex:${m.neu}"></i><i class="r" style="flex:${m.bear}"></i></div><small>${m.bull} / ${m.neu} / ${m.bear}</small></div>`;
  function deltaCell(d, up, down) {
    if (d == null) return '<span class="dF">—</span>';
    const a = d > .15 ? `<span class="dU">▲ ${sgn(d, 2)}</span>` : d < -.15 ? `<span class="dD">▼ ${sgn(d, 2)}</span>` : `<span class="dF">→ ${sgn(d, 2)}</span>`;
    return `${a}<br><small class="dF">${up}家上调 · ${down}家下调</small>`;
  }
  function spark(hist) {
    const W = 84, H = 26, yv = c => H / 2 - (c / 2) * (H / 2 - 2);
    const den = Math.max(1, hist.length - 1);
    const pts = hist.map((c, i) => c == null ? null : [i * (W / den), yv(c)]).filter(Boolean);
    const lastC = hist[hist.length - 1];
    return `<svg width="${W}" height="${H}"><line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="#d9e2ec" stroke-dasharray="2 2"/><polyline fill="none" stroke="#486581" stroke-width="1.5" points="${pts.map(p => p.join(',')).join(' ')}"/>${pts.length ? `<circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="3" fill="${consColor(lastC)}"/>` : ''}</svg>`;
  }

  function formatSummary(sm) {
    const lab = c => `<b style="color:${consColor(c)}">${consLabel(c, 'yield')}（${sgn(c, 2)}）</b>`;
    let txt = `短端共识${lab(sm.short_cons)}，10Y ${lab(sm.ten_cons)}，30Y ${lab(sm.long_cons)}`;
    if (sm.curve_cons != null) txt += `；曲线形态观点<b>${consLabel(sm.curve_cons, 'spread')}</b>`;
    txt += '。';
    if (sm.big_move) {
      const bm = sm.big_move;
      txt += ` 边际变化最大的是 <b>${bm.tenor}</b>，共识分较上周 <b class="${bm.delta > 0 ? 'dU' : 'dD'}">${sgn(bm.delta, 2)}</b>（${bm.up}家上调、${bm.down}家下调）。`;
    }
    if (sm.top_diverge) txt += ` <b>${sm.top_diverge.tenor}</b> 分歧最大（标准差 ${sm.top_diverge.disp.toFixed(2)}）。`;
    if (sm.top_layer) {
      const tl = sm.top_layer;
      txt += ` 六层框架中变化最明显的是「<b>${tl.layer}</b>」（净情绪 ${sgn(tl.delta, 1)}pt，代表主线：${(tl.phrases || []).slice(0, 2).join('、') || '—'}）。`;
    }
    return txt;
  }

  // ================= 页面一：周度驾驶舱 =================
  function renderDash(root) {
    const d = S.dash_by_week[state.week];
    const wk = S.weeks[state.week];
    root.innerHTML = `
      <div class="strip">
        <div><b>${wk.title}</b>（${wk.range}）</div>
        <div>覆盖 <b>${d.covered_brokers}/${S.brokers.length}</b> 家券商 · <b>${d.total_views}</b> 条期限观点</div>
        <div>行情基准 <b>${d.market_date}</b></div>
        <div>快照生成 ${S.generated_at}</div>
      </div>
      <div class="card summary"><span class="tag">本周一句话</span><div>${formatSummary(d.summary)}</div></div>
      <div class="grid2">
        <div class="card"><h3>收益率曲线 × 卖方目标<span class="hint">仅绘制给出明确数字点位的观点 · 实线 = 当前曲线 · 虚线 = 4周前 · ◆ = 目标中位（≥3家给点位才显示）· 点击圆点看原文</span></h3><div id="c-curve" class="chart" style="height:360px"></div></div>
        <div class="card"><h3>六层框架净情绪变化<span class="hint">本周净利多占比 − 前3周均值 · 悬停看当周真实高频主线</span></h3><div id="c-layers" class="chart" style="height:360px"></div></div>
      </div>
      <div class="card"><h3>期限观点矩阵（6个策略桶）<span class="hint">点击任意行进入期限详情 · 覆盖 ≥${S.min_sample_stats} 家才评定分歧度与顺势程度 · 目标列标注给出明确点位的家数</span></h3>${renderMatrix(d.matrix)}</div>
      <div class="grid2 eq">
        <div class="card"><h3>🔄 本周转向<span class="hint">相对该券商近4周内上一期同期限观点</span></h3>${renderChanges(d.change_ids, d.extra_changes)}</div>
        <div class="card"><h3>⚡ 分歧最大的期限<span class="hint">仅统计覆盖 ≥${S.min_sample_stats} 家的期限 · 最乐观 vs 最悲观</span></h3>${renderDivergences(d.divergences)}</div>
      </div>`;

    root.insertAdjacentHTML("beforeend", S.empty_reports.filter(r=>r.week_mon===wk.mon).map(r=>'<p>'+r.report_date+" · "+r.broker+" · "+r.title+" · "+(r.report_category==="资金流动性周报"?"资金专题·未涉国债策略":"未提及独立利率债策略")+"</p>").join(""));
    // 1) 收益率曲线 × 卖方目标
    const cv = d.curve;
    const c = mk('c-curve', {
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      legend: { top: 0, right: 0, textStyle: { color: '#627d98', fontSize: 12 }, data: cv.has_yields ? ['当前曲线', '4周前', '各家目标', '目标中位'] : ['各家目标', '目标中位'] },
      tooltip: {
        trigger: 'item',
        formatter: p => {
          if (p.seriesName === '各家目标') {
            const x = p.data.raw;
            return `<b>${x.broker}</b> · ${x.raw_tenor}<br>${dirLabel(x.dir, 'yield')} · 目标 ${x.lo.toFixed(2)}–${x.hi.toFixed(2)}%<br>${x.action} · ${x.horizon}<br><span style="color:#999">${x.phrases.join('、')}</span>`;
          }
          if (p.seriesName === '目标中位') {
            const x = p.data.raw;
            return `<b>${x.tenor}</b> 目标中位 ${x.val.toFixed(2)}%（${x.n_tgt}/${x.n}家给点位）<br>共识分 ${sgn(x.cons, 2)}`;
          }
          if (p.seriesName === '目标区间') return `${cv.tenors[p.value[0]]} 卖方目标覆盖 ${p.value[1].toFixed(2)}%–${p.value[2].toFixed(2)}%`;
          return `${p.seriesName} ${cv.tenors[p.dataIndex]}: ${(+p.value).toFixed(3)}%`;
        }
      },
      xAxis: { type: 'category', data: cv.tenors, ...AX, splitLine: { show: false } },
      yAxis: { type: 'value', scale: true, name: '收益率 %', nameTextStyle: { color: '#829ab1' }, ...AX, axisLabel: { color: '#627d98', formatter: v => v.toFixed(2) } },
      series: [
        {
          name: '目标区间', type: 'custom', z: 1,
          data: cv.bands.map(b => ({ value: [b.x, b.lo, b.hi], itemStyle: { color: consColor(b.cons) } })),
          encode: { x: 0, y: [1, 2] },
          renderItem: (p, api) => {
            const x = api.value(0), a = api.coord([x, api.value(2)]), b = api.coord([x, api.value(1)]);
            return { type: 'rect', shape: { x: a[0] - 26, y: a[1], width: 52, height: b[1] - a[1], r: 6 }, style: { fill: api.visual('color'), opacity: .15 } };
          }
        },
        ...(cv.has_yields ? [
          { name: '4周前', type: 'line', z: 2, data: cv.prev4w, lineStyle: { type: 'dashed', color: '#9fb3c8' }, itemStyle: { color: '#9fb3c8' }, symbolSize: 5 },
          {
            name: '当前曲线', type: 'line', z: 3, data: cv.current,
            lineStyle: { width: 3, color: '#14283c' }, itemStyle: { color: '#14283c' }, symbolSize: 8,
            label: { show: true, position: 'top', distance: 10, formatter: p => (+p.value).toFixed(2), color: '#14283c', fontSize: 11, backgroundColor: 'rgba(255,255,255,.88)', padding: [1, 3], borderRadius: 3 }
          }
        ] : []),
        {
          name: '各家目标', type: 'scatter', z: 4, symbolSize: 7,
          data: cv.dots.map(x => ({ value: [x.x, x.val], symbolOffset: [x.offset, 0], itemStyle: { color: dirColor(x.dir), opacity: .85 }, raw: x }))
        },
        {
          name: '目标中位', type: 'scatter', z: 5, symbol: 'diamond', symbolSize: 18,
          data: cv.meds.map(x => ({ value: [x.x, x.val], itemStyle: { color: consColor(x.cons), borderColor: '#fff', borderWidth: 1.5 }, raw: x })),
          label: { show: true, position: 'right', formatter: p => sgn(p.data.raw.cons, 1), fontWeight: 700, color: '#334e68', fontSize: 11 }
        }
      ]
    });
    if (c) c.on('click', p => {
      if (p.seriesName === '各家目标') openView(p.data.raw.vid);
      else if (p.seriesName === '目标中位') goTenor(p.data.raw.tenor);
    });

    // 2) 六层框架净情绪变化
    const ls = [...d.layer_shift].sort((a, b) => a.delta - b.delta);
    mk('c-layers', {
      grid: { left: 76, right: 54, top: 14, bottom: 24 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: ps => {
          const r = ls[ps?.[0]?.dataIndex];
          if (!r) return "";
          return `<b>${r.layer}</b><br>本周净情绪 ${sgn(r.now_score, 1)}（提及 ${r.now_total} 次，净利多 ${sgn(r.now_net, 0)}）<br>前3周均值 ${sgn(r.prev_score, 1)} · 变化 <b>${sgn(r.delta, 1)}</b><br>当周主线：${(r.phrases || []).join('、') || '—'}`;
        }
      },
      xAxis: { type: 'value', ...AX, axisLabel: { color: '#627d98', formatter: v => sgn(v, 0) } },
      yAxis: { type: 'category', data: ls.map(r => r.layer), ...AX, axisLabel: { color: '#102a43', fontWeight: 600 } },
      series: [{
        type: 'bar', barWidth: 20,
        data: ls.map(r => ({ value: r.delta, itemStyle: { color: r.delta >= 0 ? '#e8590c' : '#339af0', borderRadius: 4 } })),
        label: { show: true, position: 'right', formatter: p => sgn(p.value, 1), color: '#486581', fontSize: 11 }
      }]
    });
  }

  function renderMatrix(rows) {
    const html = rows.map(m => `<tr data-tenor="${m.tenor}">
      <td class="tn"><b>${m.tenor}</b></td>
      <td>${m.cur != null ? `<b>${fy(m.type, m.cur)}</b><br><small class="${m.wchg < 0 ? 'ydn' : 'yup'}">${sgn(m.wchg, 1)}bp 周</small>` : '<span class="dF">未接行情</span>'}</td>
      <td>${consCell(m.cons, m.type)}</td>
      <td>${stackBar(m)}</td>
      <td>${deltaCell(m.delta_cons, m.up, m.down)}</td>
      <td>${spark(m.hist)}</td>
      <td>${m.med != null ? `<b>${fy(m.type, m.med)}</b>` : '<span class="dF">点位不足</span>'}<br><small class="dF">${m.n_tgt}/${m.n}家给点位${m.space != null ? ` · ${sgn(m.space, 0)}bp` : ''}</small></td>
      <td>${lvl(m.disp, .6, 1, ['低', '中', '高'])}</td>
      <td title="共识方向与近4周行情同向程度">${lvl(m.trend_align == null ? null : Math.max(0, m.trend_align), .5, 1, ['正常', '偏高', '高度顺势'])}</td>
      <td>${m.act}</td>
      <td class="driver-col">${m.top_phrases.map(p => `<span class="chip">${p}</span>`).join('')}</td>
    </tr>`).join('');
    return `<div class="tbl-wrap"><table class="mx"><thead><tr><th>期限桶</th><th>当前 / 周变动</th><th>共识分</th><th>多 / 中 / 空</th><th>较上周</th><th>近8周共识</th><th>目标中位 / 覆盖</th><th>分歧度</th><th>顺势程度</th><th>主流操作</th><th class="driver-col">高频主线</th></tr></thead><tbody>${html}</tbody></table></div>`;
  }

  function renderChanges(ids, extra) {
    if (!ids.length) return '<div class="empty">本周无券商调整观点</div>';
    return `<div class="list">${ids.map(id => {
      const v = VMAP[id], tp = (TMAP[v.tenor] || {}).type, u = v.direction > v.prev_dir;
      return `<div class="li" data-vid="${v.id}"><span class="arrow ${u ? 'u' : 'd'}">${u ? '▲' : '▼'}</span><b>${v.broker}</b><span class="tchip">${(TMAP[v.tenor] || {}).short}</span><span>${dirLabel(v.prev_dir, tp)} → <b style="color:${dirColor(v.direction)}">${dirLabel(v.direction, tp)}</b></span>${v.gap_weeks > 1 ? `<span class="gap">较${v.gap_weeks}周前</span>` : ''}<span class="why">${v.drivers.map(d => d.phrase).join(' · ')}</span></div>`;
    }).join('')}</div>${extra > 0 ? `<div class="more">另有 ${extra} 条调整</div>` : ''}`;
  }

  function renderDivergences(list) {
    if (!list.length) return '<div class="empty">本周各期限样本不足或无显著分歧</div>';
    const side = (v, tp, cls) => `<div class="side ${cls}" data-vid="${v.id}"><b>${v.broker}</b>（${v.raw_tenor}） · <span style="color:${dirColor(v.direction)}">${dirLabel(v.direction, tp)}</span><br>目标 ${tgtTxt(v)} · ${v.action}<br>${v.drivers.map(dr => driverChip(dr)).join('')}</div>`;
    return list.map(m => `<div class="dv">
      <div class="dvh"><b data-tenor="${m.tenor}" style="cursor:pointer">${m.tenor} ›</b><span class="lv hi">分歧度 ${m.disp.toFixed(2)}</span><small>${m.bull}多 / ${m.neu}中 / ${m.bear}空</small></div>
      <div class="dvb">${side(VMAP[m.hi_id], m.type, 'bu')}<div class="vs">VS</div>${side(VMAP[m.lo_id], m.type, 'be')}</div>
    </div>`).join('');
  }

  // ================= 页面二：期限详情 =================
  function goTenor(id) { state.tenor = id; state.page = 'tenor'; render(); window.scrollTo(0, 0); }
  function renderTenor(root) {
    const t = TMAP[state.tenor];
    const dash = S.dash_by_week[state.week];
    const m = dash.matrix.find(x => x.tenor === t.id);
    const tsData = S.tenor_series[t.id];
    const unit = t.type === 'spread' ? 'bp' : '%';
    const wk = S.weeks[state.week];

    // 近4周各家最新观点
    const latest = S.brokers.map(b => {
      for (let i = state.week; i >= Math.max(0, state.week - 3); i--) {
        const found = S.views.findLast(v => v.week_idx === i && v.broker === b.name && v.tenor === t.id);
        if (found) return found;
      }
      return null;
    }).filter(Boolean).sort((a, b) => b.direction - a.direction || ((a.mid ?? 99) - (b.mid ?? 99)));

    root.innerHTML = `
      <div class="toolbar">
        <div class="pills">${S.tenors.map(x => `<button class="pill ${x.id === t.id ? 'on' : ''}" data-tenor="${x.id}">${x.id}</button>`).join('')}</div>
        <div>只看券商 <select id="tenorBroker"><option value="">全部券商</option>${S.brokers.map(b => `<option ${b.name === state.tenorBroker ? 'selected' : ''}>${b.name}</option>`).join('')}</select></div>
      </div>
      <div class="kpis">
        <div class="kpi"><label>${t.id} · 当前基准</label><b>${fy(t.type, m.cur)}</b><small class="${(m.wchg || 0) < 0 ? 'ydn' : 'yup'}">${m.cur != null ? `周 ${sgn(m.wchg, 1)}bp · 4周 ${sgn(m.mom4, 1)}bp` : '未接行情'}</small></div>
        <div class="kpi"><label>共识分（本周）</label><b style="color:${consColor(m.cons)}">${sgn(m.cons, 2)}</b><small>${consLabel(m.cons, t.type)} · 较上周 ${sgn(m.delta_cons, 2)}</small></div>
        <div class="kpi"><label>多 / 中 / 空</label><b>${m.bull} / ${m.neu} / ${m.bear}</b><small>${m.up}家上调 · ${m.down}家下调</small></div>
        <div class="kpi"><label>分歧度（标准差）</label><b>${m.disp != null ? m.disp.toFixed(2) : '样本不足'}</b><small>覆盖 ${m.n} 家（≥${S.min_sample_stats}家才评定）</small></div>
        <div class="kpi"><label>目标中位 / 隐含空间</label><b>${m.med != null ? fy(t.type, m.med) : '点位不足'}</b><small>${m.n_tgt}/${m.n}家给点位${m.space != null ? ` · ${sgn(m.space, 0)}bp` : ''}</small></div>
      </div>
      ${S.has_yields && t.ykey ? `<div class="card"><h3>${t.id} 收益率走势 × 卖方观点<span class="hint">竖条 = 给出明确点位的观点区间；无点位观点不强行画线 · 右上角选单家券商可看其连续判断</span></h3><div id="c-ty" class="chart" style="height:360px"></div></div>` : ''}
      <div class="grid2 eq">
        <div class="card"><h3>共识与分歧的周度演变<span class="hint">柱 = 多/中/空家数 · 实线 = 共识分 · 虚线 = 分歧度</span></h3><div id="c-tw" class="chart" style="height:340px"></div></div>
        <div class="card"><h3>六层框架热力图（周 × 六层）<span class="hint">红 = 净利多提及 · 绿 = 净利空提及 · 悬停查看当周研报真实主线短语</span></h3><div id="c-th" class="chart" style="height:340px"></div></div>
      </div>
      <div class="card"><h3>各家最新观点（${wk.title}）<span class="hint">近4周内有效观点 · 点击卡片查看完整逻辑、原文摘录与原 PDF</span></h3>
        <div class="vcards">${latest.filter(v=>!state.tenorBroker||v.broker===state.tenorBroker).map(v => `<div class="vc" data-vid="${v.id}" style="border-left-color:${dirColor(v.direction)}">
          <div class="vch"><b>${v.broker}</b><span class="badge" style="background:${dirColor(v.direction)}">${dirLabel(v.direction, t.type)}</span>${deltaBadge(v)}${v.week_idx < state.week ? `<span class="stale">${state.week - v.week_idx}周前</span>` : ''}</div>
          <dl class="vcm">
            <div class="vc-context"><dt>原文</dt><dd>${v.raw_tenor}</dd><dt>窗口</dt><dd>${v.horizon || '—'}</dd></div>
            <div><dt>目标</dt><dd>${tgtTxt(v)}</dd></div>
            <div><dt>操作</dt><dd>${v.action || '—'}</dd></div>
          </dl>
          <div class="vc-drivers">${v.drivers.map(dr => driverChip(dr, true)).join('')}</div><div class="vcl">${v.logic}</div></div>`).join('') || '<div class="empty">近4周无券商覆盖该期限</div>'}</div>
      </div>`;

    // 1) 收益率走势 × 卖方目标
    if (S.has_yields && t.ykey) {
      const ts = s => Date.parse(s + 'T00:00:00+08:00');
      const yArr = S.yields[t.ykey] || [];
      const tv = S.views.filter(v => v.tenor === t.id && v.week_idx <= state.week && v.target_low != null && (!state.tenorBroker || v.broker === state.tenorBroker));
      const c = mk('c-ty', {
        grid: { left: 55, right: 25, top: 28, bottom: 56 },
        tooltip: {
          trigger: 'item',
          formatter: p => {
            if (p.data && p.data.raw) {
              const v = p.data.raw;
              return `<b>${v.broker}</b> ${v.report_date}（${v.raw_tenor}）<br>${dirLabel(v.direction, t.type)} · 目标 ${tgtTxt(v)} · ${v.horizon}`;
            }
            if (!Array.isArray(p.value)) return "";
            return `${echarts.time.format(p.value[0], '{yyyy}-{MM}-{dd}')}：${(+p.value[1]).toFixed(t.type === 'spread' ? 1 : 3)}${unit}`;
          }
        },
        xAxis: { type: 'time', ...AX, splitLine: { show: false } },
        yAxis: { type: 'value', scale: true, name: unit, ...AX },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 10 }],
        series: [
          {
            name: t.id, type: 'line', showSymbol: false, z: 5,
            data: S.dates.map((dt, i) => [ts(dt), yArr[i]]),
            lineStyle: { color: '#14283c', width: 2 },
            markLine: { symbol: 'none', silent: true, data: [{ xAxis: ts(wk.mon), lineStyle: { color: '#e8590c', type: 'dashed' }, label: { formatter: '观察周', color: '#e8590c', fontSize: 11 } }] }
          },
          {
            name: '卖方目标', type: 'custom', z: 3, encode: { x: 0, y: [1, 2] },
            data: tv.map(v => ({ value: [ts(v.report_date), v.target_low, v.target_high], itemStyle: { color: dirColor(v.direction) }, raw: v })),
            renderItem: (p, api) => {
              const a = api.coord([api.value(0), api.value(2)]), b = api.coord([api.value(0), api.value(1)]), col = api.visual('color');
              return { type: 'rect', shape: { x: a[0] - 3, y: a[1], width: 6, height: Math.max(3, b[1] - a[1]), r: 2 }, style: { fill: col, opacity: .75 } };
            }
          }
        ]
      });
      if (c) c.on('click', p => { if (p.data && p.data.raw) openView(p.data.raw.id); });
    }

    // 2) 共识周度演变
    const wlist = tsData.weekly.slice(0, state.week + 1);
    mk('c-tw', {
      grid: { left: 40, right: 45, top: 30, bottom: 30 },
      legend: { top: 0, textStyle: { fontSize: 11, color: '#627d98' } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: wlist.map(x => x.short), ...AX },
      yAxis: [{ type: 'value', name: '家数', ...AX }, { type: 'value', min: -2, max: 2, name: '分', ...AX, splitLine: { show: false } }],
      series: [
        { name: '偏多', type: 'bar', stack: 'a', data: wlist.map(x => x.bull), itemStyle: { color: '#ffa8a8' } },
        { name: '中性', type: 'bar', stack: 'a', data: wlist.map(x => x.neu), itemStyle: { color: '#dee2e6' } },
        { name: '偏空', type: 'bar', stack: 'a', data: wlist.map(x => -x.bear), itemStyle: { color: '#8ce99a' } },
        { name: '共识分', type: 'line', yAxisIndex: 1, data: wlist.map(x => x.cons), lineStyle: { color: '#c92a2a', width: 2.5 }, itemStyle: { color: '#c92a2a' }, connectNulls: true },
        { name: '分歧度', type: 'line', yAxisIndex: 1, data: wlist.map(x => x.disp), lineStyle: { color: '#486581', type: 'dashed' }, itemStyle: { color: '#486581' }, symbol: 'none', connectNulls: true }
      ]
    });

    // 3) 六层框架热力图（6行永不过时，悬停看开放短语）
    const hm = tsData.heatmap;
    const cells = hm.cells.filter(c => c.x <= state.week);
    const mx = Math.max(2, hm.max_abs);
    mk('c-th', {
      grid: { left: 74, right: 16, top: 14, bottom: 52 },
      tooltip: {
        formatter: p => {
          const r = p.data.raw;
          return `<b>${hm.layers[r.y]}</b> · ${S.weeks[r.x].short}周<br>利多 ${r.bull} 次 · 利空 ${r.bear} 次（净值 ${sgn(r.net, 0)}）<br>当周主线：${r.phrases.join('、')}`;
        }
      },
      xAxis: { type: 'category', data: S.weeks.slice(0, state.week + 1).map(w => w.short), ...AX },
      yAxis: { type: 'category', data: hm.layers, ...AX, axisLabel: { color: '#102a43', fontWeight: 600, interval: 0 } },
      visualMap: {
        min: -mx, max: mx, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 10, itemHeight: 110,
        text: ['净利多', '净利空'], inRange: { color: ['#2b8a3e', '#b2f2bb', '#f1f5f9', '#ffc9c9', '#c92a2a'] }, textStyle: { fontSize: 10 }
      },
      series: [{
        type: 'heatmap',
        data: cells.map(r => ({ value: [r.x, r.y, r.net], raw: r })),
        label: { show: true, formatter: p => p.data.raw.net === 0 ? '0' : sgn(p.data.raw.net, 0), fontSize: 10, color: '#102a43' },
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 }
      }]
    });
  }

  // ================= 页面三：券商画像（立场 + 切换频率 + 轨迹，不做失真胜率） =================
  function renderBroker(root) {
    const all = S.broker_stats;
    const s = all.find(x => x.name === state.broker) || all[0];
    state.broker = s.name;
    const wk = S.weeks[state.week];

    const latest = S.tenors.map(t => {
      for (let i = state.week; i >= 0; i--) {
        const found = S.views.findLast(v => v.week_idx === i && v.broker === s.name && v.tenor === t.id);
        if (found) return found;
      }
      return null;
    }).filter(Boolean);

    root.innerHTML = `
      <div class="toolbar"><div class="pills">${S.brokers.map(b => `<button class="pill ${b.name === s.name ? 'on' : ''}" data-broker="${b.name}">${b.name}</button>`).join('')}</div></div>
      <div class="card"><h3>${s.name} · ${s.series}</h3><div class="prof">${s.tags.map(p => `<span>${p}</span>`).join('')}</div></div>
      <div class="kpis">
        <div class="kpi"><label>长期立场倾向</label><b style="color:${consColor(s.bias)}">${sgn(s.bias, 2)}</b><small>−2 偏空 … +2 偏多</small></div>
        <div class="kpi"><label>观点切换频率</label><b>${s.chg.toFixed(0)}%</b><small>相邻两期方向发生变化的比例</small></div>
        <div class="kpi"><label>给具体点位比例</label><b>${s.tgt_rate.toFixed(0)}%</b><small>给出明确收益率区间/点位的占比</small></div>
        <div class="kpi"><label>累计观点条数</label><b>${s.n} 条</b><small>覆盖 ${s.tenors} / ${S.tenors.length} 个期限桶</small></div>
        <div class="kpi"><label>最新覆盖</label><b>${latest.length} 桶</b><small>截至 ${wk.title}</small></div>
      </div>
      <div class="grid2 eq">
        <div class="card"><h3>期限桶 × 周 立场热力图<span class="hint">点击格子查看当期观点与原文</span></h3><div id="c-bh" class="chart" style="height:320px"></div></div>
        <div class="card"><h3>券商风格地图（立场倾向 × 观点切换频率）<span class="hint">右上 = 偏多且调整积极；点击气泡切换券商</span></h3><div id="c-bs" class="chart" style="height:320px"></div></div>
      </div>
      <div class="card"><h3>各期限桶最新观点（截至 ${wk.title}）</h3><div class="tbl-wrap"><table class="click"><thead><tr><th>期限桶</th><th>原文品种</th><th>日期</th><th>观点</th><th>变化</th><th>目标</th><th>窗口</th><th>操作</th><th class="driver-col">核心主线</th></tr></thead><tbody>
        ${latest.map(v => {
          const tp = (TMAP[v.tenor] || {}).type;
          return `<tr data-vid="${v.id}"><td><b>${v.tenor}</b></td><td>${v.raw_tenor}</td><td>${v.report_date}</td><td><span class="badge" style="background:${dirColor(v.direction)}">${dirLabel(v.direction, tp)}</span></td><td>${deltaBadge(v)}</td><td>${tgtTxt(v)}</td><td>${v.horizon}</td><td>${v.action}</td><td class="driver-col">${v.drivers.map(dr => driverChip(dr)).join('')}</td></tr>`;
        }).join('')}</tbody></table></div></div>`;

    // 1) 立场热力图
    const bv = S.views.filter(v => v.broker === s.name && v.week_idx <= state.week);
    const tIds = S.tenors.map(t => t.id);
    mk('c-bh', {
      grid: { left: 84, right: 16, top: 10, bottom: 56 },
      tooltip: { formatter: p => { const v = p.data.raw; return `<b>${v.tenor}</b>（${v.raw_tenor}） ${v.report_date}<br>${dirLabel(v.direction, TMAP[v.tenor].type)} · ${tgtTxt(v)}<br>${v.drivers.map(d => d.phrase).join('、')}`; } },
      xAxis: { type: 'category', data: S.weeks.slice(0, state.week + 1).map(w => w.short), ...AX },
      yAxis: { type: 'category', data: tIds, ...AX, inverse: true },
      visualMap: { type: 'piecewise', dimension: 2, orient: 'horizontal', left: 'center', bottom: 0, pieces: [{ value: 2, label: '强多', color: DIRC['2'] }, { value: 1, label: '偏多', color: DIRC['1'] }, { value: 0, label: '中性', color: DIRC['0'] }, { value: -1, label: '偏空', color: DIRC['-1'] }, { value: -2, label: '强空', color: DIRC['-2'] }], textStyle: { fontSize: 11 } },
      series: [{ type: 'heatmap', data: bv.map(v => ({ value: [v.week_idx, tIds.indexOf(v.tenor), v.direction], raw: v })), label: { show: true, formatter: p => sgn(p.value[2], 0).replace('+0', '0'), color: '#fff', fontSize: 10 }, itemStyle: { borderColor: '#fff', borderWidth: 2 } }]
    }).on('click', p => p.data && openView(p.data.raw.id));

    // 2) 风格地图：立场倾向 × 切换频率
    mk('c-bs', {
      grid: { left: 52, right: 44, top: 32, bottom: 40 },
      tooltip: { formatter: p => `<b>${p.data.name}</b><br>平均立场 ${sgn(p.value[0], 2)} · 切换频率 ${p.value[1].toFixed(0)}%<br>累计 ${p.data.n} 条观点` },
      xAxis: { type: 'value', name: '平均立场', nameLocation: 'middle', nameGap: 25, ...AX },
      yAxis: { type: 'value', name: '切换频率%', min: v => Math.max(0, Math.floor(v.min / 5) * 5 - 5), max: v => Math.ceil(v.max / 5) * 5 + 5, ...AX },
      series: [{
        type: 'scatter',
        data: all.map(x => ({ name: x.name, n: x.n, value: [x.bias, x.chg], symbolSize: 10 + x.n / 8, itemStyle: { color: x.name === s.name ? '#e8590c' : consColor(x.bias), opacity: .88 } })),
        label: { show: true, formatter: p => p.data.name.slice(0, 2), position: 'right', fontSize: 10, color: '#486581' },
        labelLayout: { hideOverlap: false, moveOverlap: 'shiftY' },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#bcccdc', type: 'dashed' }, data: [{ xAxis: 0 }] }
      }]
    }).on('click', p => { state.broker = p.data.name; render(); });
  }

  // ================= 观点详情弹窗 =================
  function openView(id) {
    const v = VMAP[id]; if (!v) return;
    const t = TMAP[v.tenor], wk = S.weeks[v.week_idx];
    const hist = S.views.filter(x => x.broker === v.broker && x.tenor === v.tenor && x.week_idx <= v.week_idx).slice(-6).reverse();
    $('#mbody').innerHTML = `
      <div class="mh">${v.broker} · ${v.tenor}（${v.raw_tenor}） <span class="badge" style="background:${dirColor(v.direction)}">${dirLabel(v.direction, t.type)}</span> ${deltaBadge(v)}</div>
      <div class="ms">${v.series} · ${v.report_date} · ${wk.title}${v.pdf_url ? ` · <a href="${v.pdf_url}" target="_blank" rel="noopener">打开原 PDF ↗</a>` : ''}</div>
      <div class="fg">
        <div><label>交投建议</label><b>${v.action}</b></div>
        <div><label>目标（${v.target_type}）</label><b>${tgtTxt(v)}</b></div>
        <div><label>观点窗口</label><b>${v.horizon}</b></div>
        <div><label>发布时基准</label><b>${fy(t.type, v.ref_val)}</b></div>
      </div>
      <div class="sec">核心逻辑</div><div>${v.logic || '—'}</div>
      <div class="sec">六层驱动</div><div>${v.drivers.map(dr => `${driverChip(dr)}<small class="dF">${dr.layer}</small> `).join(' ')}</div>
      <div class="sec">成立条件与风险</div><div>${v.cond || '—'}${v.risk ? ` · 主要风险：${v.risk}` : ''}</div>
      <div class="sec">原文摘录</div>${v.account_note ? `<p>适用账户 · ${v.account_note}</p>` : ""}${v.evidence.map(e=>`<blockquote>${e.text}<br><small>物理页 ${e.page} · ${e.pid}</small></blockquote>`).join("")}
      <div class="sec">该券商在 ${v.tenor} 上的历史轨迹</div>
      <div class="tbl-wrap"><table><thead><tr><th>日期</th><th>原文品种</th><th>观点</th><th>目标</th><th>操作</th><th>主线</th></tr></thead><tbody>
        ${hist.map(h => `<tr style="${h.id === v.id ? 'background:#fff4e6' : ''}"><td>${h.report_date}</td><td>${h.raw_tenor}</td><td><span style="color:${dirColor(h.direction)};font-weight:600">${dirLabel(h.direction, t.type)}</span></td><td>${tgtTxt(h)}</td><td>${h.action}</td><td>${h.drivers.map(d => d.phrase).join('、')}</td></tr>`).join('')}
      </tbody></table></div>`;
    $('#modal').classList.remove('hidden');
  }
  $('#modal').addEventListener('click', e => { if (e.target.id === 'modal' || e.target.classList.contains('mclose')) $('#modal').classList.add('hidden'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').classList.add('hidden'); });

  // ================= 路由与初始化 =================
  function render() {
    if (!periods?.[state.week]) return;
    S=periods[state.week];
    TMAP=Object.fromEntries(S.tenors.map(t=>[t.id,t])); VMAP=Object.fromEntries(S.views.map(v=>[v.id,v]));
    if(!S.brokers.some(b=>b.name===state.broker)) state.broker=S.brokers[0]?.name;
    clearCharts();
    document.querySelectorAll('.nav button').forEach(b => b.classList.toggle('active', b.dataset.page === state.page));
    ({ dash: renderDash, tenor: renderTenor, broker: renderBroker })[state.page]($('#app'));
  }

  document.querySelectorAll('.nav button').forEach(b => b.addEventListener('click', () => { state.page = b.dataset.page; render(); }));
  $('#weekSel').addEventListener('change', e => { state.week = +e.target.value; render(); });
  $('#app').addEventListener('click', e => {
    const v = e.target.closest('[data-vid]'); if (v) { openView(+v.dataset.vid); return; }
    const t = e.target.closest('[data-tenor]'); if (t) { goTenor(t.dataset.tenor); return; }
    const b = e.target.closest('[data-broker]'); if (b) { state.broker = b.dataset.broker; state.page='broker'; render(); return; }
  });
  $('#app').addEventListener('change', e => {
    if (e.target.id === 'tenorBroker') { state.tenorBroker = e.target.value; render(); }
  });

  fetch('./data/snapshot.json')
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(data => {
      const safe = x => typeof x==="string" ? x.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])) : Array.isArray(x) ? x.map(safe) : x && typeof x==="object" ? Object.fromEntries(Object.entries(x).map(([k,v])=>[k,safe(v)])) : x;
      periods=safe(data.as_of); S=periods[periods.length-1];
      if(!S){$("#app").textContent="暂无已归档研报";return;}
      TMAP = Object.fromEntries(S.tenors.map(t => [t.id, t]));
      VMAP = Object.fromEntries(S.views.map(v => [v.id, v]));
      state.week = Math.max(0, S.weeks.length - 1);
      state.broker = S.brokers[0] ? S.brokers[0].name : '';
      const ws = $('#weekSel');
      ws.innerHTML = S.weeks.map(w => `<option value="${w.idx}">${w.title}（${w.range}）</option>`).reverse().join('');
      ws.value = String(state.week);
      render();
    })
    .catch(err => {
      $('#app').innerHTML = `<div class="card">无法载入 ./data/snapshot.json：${err.message}。请先运行 <code>python pipeline/build_snapshot.py</code>。</div>`;
    });
})();
