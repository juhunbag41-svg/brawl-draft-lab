(function () {
  'use strict';
  const E = window.DraftEngine;
  const MIN_GAMES = 30; // 추천 후보가 되려면 이 맵에서 최소 이만큼 등장해야 한다
  const PICK_ORDER = {
    blue: ['blue', 'red', 'red', 'blue', 'blue', 'red'],
    red: ['red', 'blue', 'blue', 'red', 'red', 'blue'],
  };
  const MODE_KO = {
    gemGrab: '젬 그랩', brawlBall: '브롤 볼', bounty: '바운티', heist: '하이스트', hotZone: '핫 존',
    knockout: '녹아웃', wipeout: '와이프아웃', brawlHockey: '브롤 하키', basketBrawl: '바스켓 브롤',
    volleyBrawl: '발리 브롤', siege: '시즈',
  };
  const TEAM_KO = { blue: '우리 팀', red: '상대 팀' };

  let STATS = null;
  let model = null;
  let recTimer = null;
  const st = { map: '', first: 'blue', phase: 'ban', banTarget: 'blue', bans: { blue: [], red: [] }, picks: { blue: [], red: [] } };
  const history = [];

  const $ = (id) => document.getElementById(id);
  const pct = (x, d = 1) => (x * 100).toFixed(d) + '%';
  // logit 변화량을 50% 근처의 승률 변화(%p)로 근사: dP ≈ 0.25·dz
  const pp = (dz) => { const v = dz * 25; return (v >= 0 ? '+' : '') + v.toFixed(1) + '%p'; };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function hue(name) { let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360; return `hsl(${h} 60% 52%)`; }
  function store(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* 저장 불가 환경 */ } }
  function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

  function order() { return PICK_ORDER[st.first]; }
  function pickCount() { return st.picks.blue.length + st.picks.red.length; }
  function usedSet() { return new Set([...st.bans.blue, ...st.bans.red, ...st.picks.blue, ...st.picks.red]); }
  function snapshot() { history.push(JSON.stringify(st)); }
  function modeName(m) { return MODE_KO[m] || m || '기타'; }

  async function load() {
    try {
      const r = await fetch('data/stats.json', { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      STATS = await r.json();
    } catch (e) {
      $('dataInfo').textContent = '데이터를 불러오지 못했습니다. 첫 수집이 끝났는지 확인하세요.';
      return;
    }
    const updated = new Date(STATS.updated);
    $('dataInfo').textContent = `최근 ${STATS.window_days}일 랭크전 ${STATS.total_games.toLocaleString('ko-KR')}판 · ${updated.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })} 갱신`;
    $('demoBanner').classList.toggle('hidden', !STATS.demo);

    const maps = Object.entries(STATS.maps).sort((a, b) =>
      modeName(a[1].mode).localeCompare(modeName(b[1].mode), 'ko') || a[0].localeCompare(b[0]));
    for (const sel of [$('mapSelect'), $('tierMapSelect')]) {
      sel.innerHTML = '';
      let group = null, cur = null;
      for (const [name, m] of maps) {
        if (modeName(m.mode) !== cur) {
          cur = modeName(m.mode);
          group = document.createElement('optgroup');
          group.label = cur;
          sel.appendChild(group);
        }
        const o = document.createElement('option');
        o.value = name; o.textContent = `${name} (${m.games.toLocaleString('ko-KR')}판)`;
        group.appendChild(o);
      }
    }
    if (maps.length === 0) { $('dataInfo').textContent = '아직 집계된 맵이 없습니다.'; return; }
    const saved = recall('bdl.map');
    const first = saved && STATS.maps[saved] ? saved : maps[0][0];
    const savedFirst = recall('bdl.first');
    if (savedFirst === 'blue' || savedFirst === 'red') st.first = savedFirst;
    setMap(first);
    renderTier(first);
  }

  function setMap(name) {
    st.map = name;
    $('mapSelect').value = name;
    store('bdl.map', name);
    model = E.buildModel(STATS.maps[name], STATS.brawlers, MIN_GAMES);
    render();
  }

  function ids(names) { return names.map((n) => model.index.get(n)).filter((i) => i !== undefined); }

  function choose(name) {
    if (!model || usedSet().has(name)) return;
    if (st.phase === 'ban') {
      let t = st.banTarget;
      if (st.bans[t].length >= 3) t = t === 'blue' ? 'red' : 'blue';
      if (st.bans[t].length >= 3) return;
      snapshot();
      st.bans[t].push(name);
      if (st.bans[t].length >= 3) {
        const other = t === 'blue' ? 'red' : 'blue';
        if (st.bans[other].length < 3) st.banTarget = other;
      }
    } else {
      const n = pickCount();
      if (n >= 6) return;
      snapshot();
      st.picks[order()[n]].push(name);
    }
    render();
  }

  // ---------- 그리기 ----------
  function render() {
    renderControls();
    renderBoard();
    renderGrid();
    scheduleRecs();
  }

  function renderControls() {
    for (const b of $('firstPick').querySelectorAll('button')) {
      b.classList.toggle('on', b.dataset.first === st.first);
      b.disabled = pickCount() > 0;
    }
    for (const b of $('banTarget').querySelectorAll('button')) b.classList.toggle('on', b.dataset.target === st.banTarget);
    $('undoBtn').disabled = history.length === 0;
    $('banControls').classList.toggle('hidden', st.phase !== 'ban');
  }

  function renderBoard() {
    const ord = order();
    const n = pickCount();
    let phase;
    if (st.phase === 'ban') {
      phase = `밴 단계 · 밴된 브롤러를 입력하세요 (우리 ${st.bans.blue.length}/3 · 상대 ${st.bans.red.length}/3)`;
    } else if (n < 6) {
      phase = `${n + 1}번째 픽 · <span class="who-${ord[n]}">${TEAM_KO[ord[n]]}</span> 차례`;
    } else {
      phase = '드래프트 완료';
    }
    $('phase').innerHTML = phase;

    for (const team of ['blue', 'red']) {
      const nums = ord.map((t, i) => (t === team ? i + 1 : 0)).filter(Boolean);
      const picksEl = $(team + 'Picks');
      picksEl.innerHTML = '';
      for (let i = 0; i < 3; i++) {
        const d = document.createElement('div');
        const name = st.picks[team][i];
        const isCurrent = st.phase === 'pick' && n < 6 && ord[n] === team && st.picks[team].length === i;
        d.className = 'slot' + (name ? ' filled' : '') + (isCurrent ? ' current' : '');
        d.innerHTML = `<span class="num">${nums[i]}픽</span>${name ? esc(name) : ''}`;
        picksEl.appendChild(d);
      }
      const bansEl = $(team + 'Bans');
      bansEl.innerHTML = '';
      for (let i = 0; i < 3; i++) {
        const d = document.createElement('div');
        const name = st.bans[team][i];
        d.className = 'slot' + (name ? ' filled' : '') + (name && st.phase === 'ban' ? ' removable' : '');
        d.textContent = name || '밴';
        if (name && st.phase === 'ban') {
          d.title = '눌러서 빼기';
          d.addEventListener('click', () => { snapshot(); st.bans[team].splice(i, 1); render(); });
        }
        bansEl.appendChild(d);
      }
    }
  }

  function renderGrid() {
    const q = $('search').value.trim().toUpperCase();
    const used = usedSet();
    const mapStats = STATS.maps[st.map];
    const grid = $('grid');
    grid.innerHTML = '';
    const frag = document.createDocumentFragment();
    for (const name of STATS.brawlers) {
      if (q && !name.includes(q)) continue;
      const v = mapStats.brawlers[name];
      const b = document.createElement('button');
      b.className = 'tile' + (!v || v[0] < MIN_GAMES ? ' rare' : '');
      b.style.setProperty('--tile', hue(name));
      b.disabled = used.has(name) || (st.phase === 'pick' && pickCount() >= 6);
      b.innerHTML = `<b>${esc(name)}</b><span>${v ? `승률 ${pct(v[1] / v[0], 0)} · ${v[0].toLocaleString('ko-KR')}판` : '데이터 없음'}</span>`;
      b.addEventListener('click', () => choose(name));
      frag.appendChild(b);
    }
    grid.appendChild(frag);
  }

  function setMeter(p, label) {
    $('winMeter').classList.remove('hidden');
    $('winMeter').querySelector('.meter-label span').textContent = label;
    $('winValue').textContent = pct(p);
    $('winFill').style.width = (p * 100).toFixed(1) + '%';
  }

  function scheduleRecs() {
    clearTimeout(recTimer);
    $('recList').innerHTML = '';
    if (st.phase === 'ban') { renderBanRecs(); return; }
    $('recTitle').textContent = '계산 중…';
    $('recNote').textContent = '';
    // 화면이 먼저 그려지도록 탐색은 다음 틱에 실행한다.
    recTimer = setTimeout(renderPickRecs, 30);
  }

  function renderBanRecs() {
    $('winMeter').classList.add('hidden');
    $('recTitle').textContent = '밴 후보';
    $('recNote').textContent = '이 맵에서 강도가 높은 순입니다. 누르면 밴으로 입력됩니다.';
    const used = usedSet();
    const list = model.eligible.filter((i) => !used.has(model.names[i]))
      .sort((a, b) => model.beta[b] - model.beta[a]).slice(0, 10);
    const ol = $('recList');
    list.forEach((i, k) => {
      const name = model.names[i];
      const li = document.createElement('li');
      li.className = 'rec';
      li.tabIndex = 0;
      li.innerHTML = `<span class="rank">${k + 1}</span><span class="name">${esc(name)}</span>
        <span class="pct ${model.beta[i] >= 0 ? 'up' : 'down'}">${pp(model.beta[i])}</span>
        <span class="detail"><span>원시 승률 ${pct(model.wins[i] / model.games[i])}</span>
        <span>픽률 ${pct(model.games[i] / model.totalGames)}</span><span>${model.games[i].toLocaleString('ko-KR')}판</span></span>`;
      li.addEventListener('click', () => choose(name));
      ol.appendChild(li);
    });
  }

  function renderPickRecs() {
    const ord = order();
    const n = pickCount();
    const blue = ids(st.picks.blue), red = ids(st.picks.red);
    const banned = ids([...st.bans.blue, ...st.bans.red]);
    if (n >= 6) {
      const z = E.evaluate(model, blue, red);
      setMeter(E.sigmoid(z), '최종 조합 기준 우리 팀 예상 승률');
      $('recTitle').textContent = '드래프트 완료';
      $('recNote').textContent = '되돌리기로 다른 선택을 비교해 볼 수 있습니다.';
      renderBreakdown(blue, red);
      return;
    }
    const t0 = performance.now();
    const res = E.recommend(model, { blue, red, banned, order: ord, step: n }, 12);
    const ms = performance.now() - t0;
    const team = ord[n];
    if (res.items.length) setMeter(res.items[0].winBlue, '양 팀이 최선으로 고를 때 우리 팀 예상 승률');
    $('recTitle').textContent = team === 'blue' ? '우리 팀 추천 픽' : '상대 팀이 고를 만한 픽';
    $('recNote').textContent = `오른쪽 숫자 = 그 픽 이후 양 팀이 최선으로 고를 때의 우리 팀 예상 승률. 남은 픽을 끝까지 탐색 (${res.nodes.toLocaleString('ko-KR')}개 상태, ${ms.toFixed(0)}ms)`;
    const ol = $('recList');
    ol.innerHTML = '';
    const nextTeams = ord.slice(n + 1);
    res.items.forEach((it, k) => {
      const li = document.createElement('li');
      li.className = 'rec';
      li.tabIndex = 0;
      const p = it.immediate;
      const cls = (v) => (v >= 0 ? 'up' : 'down');
      const line = it.line.map((nm, j) => `<span class="who-${nextTeams[j]}" style="color:var(--${nextTeams[j]})">${esc(nm)}</span>`).join(' → ');
      const wr = it.games ? `승률 ${pct(it.wins / it.games)} (${it.games.toLocaleString('ko-KR')}판)` : '데이터 없음';
      li.innerHTML = `<span class="rank">${k + 1}</span><span class="name">${esc(it.name)}</span>
        <span class="pct">${pct(it.winBlue)}</span>
        <span class="detail"><span>강도 <b class="${cls(p.base)}">${pp(p.base)}</b></span>
        ${Math.abs(p.syn) > 1e-9 ? `<span>시너지 <b class="${cls(p.syn)}">${pp(p.syn)}</b></span>` : ''}
        ${Math.abs(p.ctr) > 1e-9 ? `<span>카운터 <b class="${cls(p.ctr)}">${pp(p.ctr)}</b></span>` : ''}
        <span>${wr}</span></span>
        ${line ? `<span class="line">이후 예상: ${line}</span>` : ''}`;
      li.addEventListener('click', () => choose(it.name));
      ol.appendChild(li);
    });
  }

  function renderBreakdown(blue, red) {
    const ol = $('recList');
    ol.innerHTML = '';
    const own = { blue: [], red: [] };
    const rows = [];
    // evaluate() 와 같은 순서로 더해야 카운터가 한 번씩만 들어간다.
    for (const c of blue) { rows.push(['blue', c, E.parts(model, c, own.blue, [], 1)]); own.blue.push(c); }
    for (const c of red) { rows.push(['red', c, E.parts(model, c, own.red, blue, -1)]); own.red.push(c); }
    for (const [team, c, p] of rows) {
      const li = document.createElement('li');
      li.className = 'rec';
      li.style.cursor = 'default';
      const cls = (v) => (v >= 0 ? 'up' : 'down');
      li.innerHTML = `<span class="rank" style="color:var(--${team})">●</span><span class="name">${esc(model.names[c])}</span>
        <span class="pct ${cls(team === 'blue' ? p.total : -p.total)}">${pp(team === 'blue' ? p.total : -p.total)}</span>
        <span class="detail"><span>${TEAM_KO[team]}</span><span>강도 ${pp(p.base)}</span>
        ${Math.abs(p.syn) > 1e-9 ? `<span>시너지 ${pp(p.syn)}</span>` : ''}${Math.abs(p.ctr) > 1e-9 ? `<span>카운터 ${pp(p.ctr)}</span>` : ''}</span>`;
      ol.appendChild(li);
    }
    const note = document.createElement('li');
    note.className = 'muted small';
    note.textContent = '오른쪽 숫자 = 우리 팀 승률에 준 영향(근사). 상대 팀 행의 카운터는 우리 팀과의 상성입니다.';
    ol.appendChild(note);
  }

  function renderTier(name) {
    $('tierMapSelect').value = name;
    const m = STATS.maps[name];
    const rows = Object.entries(m.brawlers).sort((a, b) => b[1][2] - a[1][2]);
    const maxAbs = Math.max(0.01, ...rows.map((r) => Math.abs(r[1][2])));
    let html = '<thead><tr><th>#</th><th>브롤러</th><th>강도</th><th>원시 승률</th><th>픽률</th><th>판수</th></tr></thead><tbody>';
    rows.forEach(([n, v], i) => {
      const w = (Math.abs(v[2]) / maxAbs * 60).toFixed(0);
      const color = v[2] >= 0 ? 'var(--good)' : 'var(--bad)';
      html += `<tr><td>${i + 1}</td><td>${esc(n)}${v[0] < 300 ? ' <span class="muted small">(표본 적음)</span>' : ''}</td>
        <td>${pp(v[2])}<span class="bar" style="width:${w}px;background:${color}"></span></td>
        <td>${pct(v[1] / v[0])}</td><td>${pct(v[0] / m.games)}</td><td>${v[0].toLocaleString('ko-KR')}</td></tr>`;
    });
    $('tierTable').innerHTML = html + '</tbody>';
  }

  // ---------- 이벤트 ----------
  $('mapSelect').addEventListener('change', (e) => setMap(e.target.value));
  $('tierMapSelect').addEventListener('change', (e) => renderTier(e.target.value));
  $('search').addEventListener('input', renderGrid);
  $('firstPick').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || pickCount() > 0) return;
    st.first = b.dataset.first;
    store('bdl.first', st.first);
    render();
  });
  $('banTarget').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    st.banTarget = b.dataset.target;
    renderControls();
  });
  $('startPicksBtn').addEventListener('click', () => { snapshot(); st.phase = 'pick'; render(); });
  $('undoBtn').addEventListener('click', () => {
    if (!history.length) return;
    Object.assign(st, JSON.parse(history.pop()));
    render();
  });
  $('resetBtn').addEventListener('click', () => {
    snapshot();
    st.phase = 'ban'; st.banTarget = 'blue';
    st.bans = { blue: [], red: [] }; st.picks = { blue: [], red: [] };
    render();
  });
  for (const t of document.querySelectorAll('.tab')) {
    t.addEventListener('click', () => {
      for (const x of document.querySelectorAll('.tab')) x.classList.toggle('active', x === t);
      for (const p of document.querySelectorAll('.tabpane')) p.classList.toggle('hidden', p.id !== 'tab-' + t.dataset.tab);
      if (t.dataset.tab === 'tier' && STATS) renderTier($('tierMapSelect').value || st.map);
    });
  }

  load();
})();
