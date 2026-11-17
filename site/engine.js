/* 드래프트 추천 엔진
 * 승리 확률 모델 (aggregate.py 와 같은 식):
 *   z = Σβ(우리) − Σβ(상대) + Σγ(우리 쌍) − Σγ(상대 쌍) + Σ δ(우리 a, 상대 c),  P(우리 승) = σ(z)
 * 브롤러 c 를 팀에 넣을 때 z 의 변화량은 s·(β_c + Σ_own γ_c· + Σ_enemy δ_c·) 로 바로 계산된다
 * (s = +1 우리 팀, −1 상대 팀). 이 덕분에 탐색 중 매번 전체를 다시 계산할 필요가 없다.
 * 탐색: 미니맥스 + 알파-베타 가지치기. 우리 팀은 z 최대화, 상대 팀은 z 최소화.
 * 단계마다 즉시 이득이 큰 후보 K명만 펼치는 빔 탐색이라, 결과는 '근사' 최선이다.
 */
(function (root) {
  'use strict';

  function sigmoid(z) { return 1 / (1 + Math.exp(-z)); }

  function buildModel(mapStats, allNames, minGames) {
    const names = allNames.slice();
    const index = new Map(names.map((n, i) => [n, i]));
    const N = names.length;
    const beta = new Float64Array(N);
    const games = new Int32Array(N);
    const wins = new Int32Array(N);
    const syn = new Float64Array(N * N);
    const ctr = new Float64Array(N * N);
    for (const [name, v] of Object.entries(mapStats.brawlers || {})) {
      const i = index.get(name);
      if (i === undefined) continue;
      games[i] = v[0]; wins[i] = v[1]; beta[i] = v[2];
    }
    for (const [key, v] of Object.entries(mapStats.syn || {})) {
      const [a, b] = key.split('|').map((n) => index.get(n));
      if (a === undefined || b === undefined) continue;
      syn[a * N + b] = v[0]; syn[b * N + a] = v[0];
    }
    for (const [key, v] of Object.entries(mapStats.ctr || {})) {
      const [a, b] = key.split('|').map((n) => index.get(n));
      if (a === undefined || b === undefined) continue;
      ctr[a * N + b] = v[0]; ctr[b * N + a] = -v[0];
    }
    const eligible = [];
    for (let i = 0; i < N; i++) if (games[i] >= minGames) eligible.push(i);
    return { names, index, N, beta, games, wins, syn, ctr, eligible, totalGames: mapStats.games };
  }

  // 브롤러 c 를 own 팀에 넣을 때의 z 변화량과 그 구성 요소
  function parts(m, c, own, enemy, s) {
    let sy = 0, ct = 0;
    const cN = c * m.N;
    for (let i = 0; i < own.length; i++) sy += m.syn[cN + own[i]];
    for (let i = 0; i < enemy.length; i++) ct += m.ctr[cN + enemy[i]];
    return { base: s * m.beta[c], syn: s * sy, ctr: s * ct, total: s * (m.beta[c] + sy + ct) };
  }

  function evaluate(m, blue, red) {
    let z = 0;
    const tmpB = [], tmpR = [];
    for (const c of blue) { z += parts(m, c, tmpB, tmpR, 1).total; tmpB.push(c); }
    // 상대 팀은 우리 팀이 모두 들어간 뒤에 넣어야 카운터가 정확히 한 번씩 계산된다.
    for (const c of red) { z += parts(m, c, tmpR, tmpB, -1).total; tmpR.push(c); }
    return z;
  }

  function beamWidth(remaining) {
    if (remaining >= 4) return 5;
    if (remaining >= 2) return 7;
    return 12;
  }

  /**
   * state: { blue: [id], red: [id], banned: [id], order: ['blue'|'red', ...6], step }
   * 반환: 현재 차례 팀의 후보별 결과(그 팀 관점에서 좋은 순)
   */
  function recommend(m, state, limit) {
    const order = state.order;
    const picks = { blue: state.blue.slice(), red: state.red.slice() };
    const used = new Uint8Array(m.N);
    for (const id of state.banned) used[id] = 1;
    for (const id of picks.blue) used[id] = 1;
    for (const id of picks.red) used[id] = 1;
    let nodes = 0;
    const z0 = evaluate(m, picks.blue, picks.red);

    function candidates(step, k) {
      const team = order[step];
      const s = team === 'blue' ? 1 : -1;
      const own = picks[team], enemy = picks[team === 'blue' ? 'red' : 'blue'];
      const list = [];
      for (const c of m.eligible) {
        if (used[c]) continue;
        list.push([c, parts(m, c, own, enemy, s).total]);
      }
      // s·Δ 가 큰 순서 = 그 팀에게 즉시 이득이 큰 순서
      list.sort((a, b) => s * (b[1] - a[1]));
      return k ? list.slice(0, k) : list;
    }

    function search(step, z, alpha, beta) {
      nodes++;
      if (step >= order.length) return { v: z, line: [] };
      const team = order[step];
      const cands = candidates(step, beamWidth(order.length - step));
      if (cands.length === 0) return { v: z, line: [] };
      const maximize = team === 'blue';
      let best = { v: maximize ? -Infinity : Infinity, line: [] };
      for (const [c, d] of cands) {
        used[c] = 1; picks[team].push(c);
        const r = search(step + 1, z + d, alpha, beta);
        picks[team].pop(); used[c] = 0;
        if (maximize ? r.v > best.v : r.v < best.v) best = { v: r.v, line: [c, ...r.line] };
        if (maximize) alpha = Math.max(alpha, r.v); else beta = Math.min(beta, r.v);
        if (beta <= alpha) break;
      }
      return best;
    }

    const step = state.step;
    if (step >= order.length) return { items: [], nodes: 0, finalZ: z0 };
    const team = order[step];
    const s = team === 'blue' ? 1 : -1;
    const own = picks[team], enemy = picks[team === 'blue' ? 'red' : 'blue'];
    const rootCands = candidates(step, 0);
    const items = [];
    for (const [c, d] of rootCands) {
      const p = parts(m, c, own, enemy, s);
      used[c] = 1; picks[team].push(c);
      const r = search(step + 1, z0 + d, -Infinity, Infinity);
      picks[team].pop(); used[c] = 0;
      items.push({
        id: c, name: m.names[c], z: r.v, winBlue: sigmoid(r.v),
        immediate: p, line: r.line.map((id) => m.names[id]),
        games: m.games[c], wins: m.wins[c],
      });
    }
    items.sort((a, b) => s * (b.z - a.z));
    return { items: limit ? items.slice(0, limit) : items, nodes, team, finalZ: z0 };
  }

  const api = { sigmoid, buildModel, recommend, evaluate, parts };
  root.DraftEngine = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
