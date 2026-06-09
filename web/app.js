/* Claudio Symphony — session-centric control + living constellation */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = {
  get: (u) => fetch(u).then(r => r.json()),
  post: (u, b) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) }).then(r => r.json()),
};
const PALETTE = ['#e8b25c', '#ffd98a', '#8fc0a6', '#8ab6d6', '#e58c66', '#c98c34', '#d9b07a', '#9ec9b0'];
let STATE = null, DETAIL = null;
let FOCUS = { kind: 'global' };        // {kind:'global'} | {kind:'session', id, s}
let lastVoiceTs = {}, lastEventTs = {};
const cssEsc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s;

const toast = (() => { const el = $('#toast'); let t;
  return (h) => { el.innerHTML = h; el.classList.add('show'); clearTimeout(t); t = setTimeout(() => el.classList.remove('show'), 2200); }; })();
const fmt = (n, d = 2) => (n == null ? '—' : (+n).toFixed(d));
const setFill = (el) => { const p = (el.value - el.min) / (el.max - el.min) * 100; el.style.setProperty('--p', p + '%'); };
function ageLabel(a) { if (a == null) return ''; if (a < 60) return Math.round(a) + 's'; if (a < 3600) return Math.round(a / 60) + 'm'; return Math.round(a / 3600) + 'h'; }

/* which preset the stage is currently editing (depends on focus) */
function editPreset() {
  if (FOCUS.kind === 'session' && FOCUS.s && FOCUS.s.preset) return FOCUS.s.preset;
  return STATE ? STATE.active : null;
}

/* ---------------- boot ---------------- */
async function boot() {
  await loadState();
  wireGlobal();
  setFocus({ kind: 'global' });
  startActivityLoop(); startSky();
  setInterval(syncExternal, 4000);
}
async function loadState() {
  STATE = await api.get('/api/state');
  const m = $('#master'); m.value = STATE.master_gain; setFill(m); $('#masterVal').textContent = fmt(STATE.master_gain, 2);
  setPower(!STATE.muted); document.body.classList.toggle('is-muted', STATE.muted);
  $('#npName').textContent = STATE.active;
  renderRail();
}
function setPower(on) { const p = $('#power'); p.classList.toggle('on', on); p.classList.toggle('off', !on); p.querySelector('.lbl').textContent = on ? 'ON' : 'OFF'; }

/* ---------------- left rail: sessions ---------------- */
function renderRail() {
  const wrap = $('#sessList'); wrap.innerHTML = '';
  const sess = (STATE.sessions || []).filter(s => !s.ended || s.age < 300);
  $('#sessCount').textContent = sess.length ? sess.length + ' open' : '';

  // Global default pseudo-session
  const g = document.createElement('div');
  g.className = 'srail-item global' + (FOCUS.kind === 'global' ? ' focused' : '');
  g.innerHTML = `<div class="si-top"><span class="si-dot" style="background:var(--gold);box-shadow:0 0 7px var(--gold)"></span>
    <span class="si-name">Global default</span></div>
    <div class="si-preset">${STATE.active} <span class="via">· when no session matches</span></div>`;
  g.onclick = () => setFocus({ kind: 'global' });
  wrap.appendChild(g);

  if (!sess.length) {
    const e = document.createElement('div'); e.className = 'rempty'; e.style.padding = '14px 4px';
    e.textContent = 'no open Claude sessions yet — start one in any project and it appears here.';
    wrap.appendChild(e); return;
  }
  sess.forEach(s => {
    const recent = s.age < 90 && !s.ended;
    const focused = FOCUS.kind === 'session' && FOCUS.id === s.id;
    const badges = [s.pinned && `<span class="ov pin">📌</span>`, s.scale && `<span class="ov">♪</span>`, s.song && `<span class="ov">🎹</span>`].filter(Boolean).join('');
    const el = document.createElement('div');
    el.className = 'srail-item' + (recent ? ' recent' : '') + (focused ? ' focused' : '');
    el.innerHTML = `<div class="si-top"><span class="si-dot"></span><span class="si-name" title="${s.cwd || s.id}">${s.base}</span><span class="si-age">${ageLabel(s.age)}</span></div>
      <div class="si-preset">${s.preset || '—'} <span class="via">via ${s.source}</span></div>
      ${badges ? `<div class="si-badges">${badges}</div>` : ''}`;
    el.onclick = () => setFocus({ kind: 'session', id: s.id, s });
    wrap.appendChild(el);
  });
}

function setFocus(focus) {
  // refresh the session object from latest state if it's a session
  if (focus.kind === 'session') {
    const fresh = (STATE.sessions || []).find(x => x.id === focus.id);
    if (fresh) focus.s = fresh;
  }
  FOCUS = focus;
  renderRail();
  loadFocus();
}
async function loadFocus() {
  const name = editPreset();
  if (!name) return;
  DETAIL = await api.get('/api/preset?name=' + encodeURIComponent(name));
  lastVoiceTs = {}; lastEventTs = {};
  renderStage(); buildNodes();
}

/* ---------------- stage ---------------- */
function renderStage() {
  const isGlobal = FOCUS.kind === 'global';
  const s = FOCUS.s;
  $('#stageTitle').textContent = isGlobal ? 'Global default' : (s ? s.base : '—');
  const ct = $('#ctxTag');
  ct.className = 'ctx-tag ' + (isGlobal ? 'global' : 'session');
  ct.textContent = isGlobal ? 'default' : 'session';
  $('#stageSub').textContent = isGlobal
    ? `tuning ${DETAIL.name} — plays whenever no session rule/pin matches`
    : `tuning ${DETAIL.name}${s && s.source ? ` · resolved via ${s.source}` : ''} — ${DETAIL.description || ''}`.slice(0, 150);
  renderSessionStrip();
  const rs = $('#reverbScale'); rs.value = DETAIL.reverb_scale ?? 1; setFill(rs); $('#reverbScaleVal').textContent = fmt(rs.value, 2) + '×';
  $('#voiceHint').textContent = `${DETAIL.voices.length} voices · ${DETAIL.scale_pitches.length} notes`;
  $('#musicScaleHint').textContent = isGlobal ? 'global default key' : 'global default (override per-session at right)';
  renderVoices(); renderEvents(); renderMusic(); renderActions(); renderSettings(); renderRules();
}

function renderSessionStrip() {
  const strip = $('#sessionStrip'); strip.innerHTML = '';
  const m = STATE.music || { scales: [], songs: [] };
  // preset chip → opens browser
  const chip = document.createElement('div'); chip.className = 'ss-field';
  chip.innerHTML = `<label>${FOCUS.kind === 'global' ? 'default preset' : 'session preset'}</label>
    <div class="preset-chip" id="presetChip"><span class="pc-name">${DETAIL.name}</span><span class="pc-ico">⊞ change</span></div>`;
  chip.querySelector('#presetChip').onclick = openBrowser;
  strip.appendChild(chip);

  if (FOCUS.kind === 'session' && FOCUS.s) {
    const s = FOCUS.s;
    const scaleOpts = `<option value="__none__"${!s.scale ? ' selected' : ''}>default</option>` +
      (m.scales || []).map(x => `<option ${x === s.scale ? 'selected' : ''}>${x}</option>`).join('');
    const songOpts = `<option value="__none__"${!s.song ? ' selected' : ''}>off</option>` +
      (m.songs || []).map(x => `<option ${x === s.song ? 'selected' : ''}>${x}</option>`).join('');
    const sc = document.createElement('div'); sc.className = 'ss-field';
    sc.innerHTML = `<label>scale</label><select class="vsel ${s.scale ? '' : 'silent'}" id="ssScale">${scaleOpts}</select>`;
    sc.querySelector('#ssScale').onchange = e => { api.post('/api/session/scale', { id: s.id, scale: e.target.value === '__none__' ? null : e.target.value }); s.scale = e.target.value === '__none__' ? null : e.target.value; e.target.classList.toggle('silent', !s.scale); toast(`${s.base} scale → ${s.scale || 'default'}`); renderRail(); };
    strip.appendChild(sc);
    const sg = document.createElement('div'); sg.className = 'ss-field';
    sg.innerHTML = `<label>song</label><select class="vsel ${s.song ? '' : 'silent'}" id="ssSong">${songOpts}</select>`;
    sg.querySelector('#ssSong').onchange = e => { api.post('/api/session/song', { id: s.id, song: e.target.value === '__none__' ? null : e.target.value }); s.song = e.target.value === '__none__' ? null : e.target.value; e.target.classList.toggle('silent', !s.song); toast(`${s.base} song → ${s.song || 'off'}`); renderRail(); };
    strip.appendChild(sg);
    if (s.pinned) {
      const un = document.createElement('div'); un.className = 'ss-field';
      un.innerHTML = `<label>&nbsp;</label><button class="btn ghost sm" id="ssUnpin">📌 unpin</button>`;
      un.querySelector('#ssUnpin').onclick = async () => { await api.post('/api/session/pin', { id: s.id, preset: '__none__' }); s.pinned = null; toast(`${s.base} follows rules`); renderRail(); renderSessionStrip(); };
      strip.appendChild(un);
    }
  }
}

/* ---------------- preset browser overlay ---------------- */
function openBrowser() {
  const isGlobal = FOCUS.kind === 'global';
  $('#browserTitle').textContent = isGlobal ? 'Set the global default' : `Choose a preset for ${FOCUS.s.base}`;
  $('#browserSub').textContent = isGlobal
    ? 'plays whenever no session pin or directory rule matches'
    : 'pins this session to the preset — overrides rules + default';
  $('#browserSearch').value = '';
  renderBrowser('');
  $('#browser').hidden = false;
  setTimeout(() => $('#browserSearch').focus(), 50);
}
function closeBrowser() { $('#browser').hidden = true; }

/* ---------------- tip / buy-me-a-coffee ---------------- */
let DONATE = null;
async function openTip() {
  $('#tipModal').hidden = false;
  if (!DONATE) { try { DONATE = await api.get('/api/donate'); } catch (e) { DONATE = { methods: [] }; } }
  $('#tipTitle').textContent = DONATE.title || '☕ Buy me a coffee';
  $('#tipBlurb').textContent = DONATE.blurb || '';
  renderTip();
}
function closeTip() { $('#tipModal').hidden = true; }
function renderTip() {
  const grid = $('#tipGrid'); grid.innerHTML = '';
  (DONATE.methods || []).forEach(m => {
    const card = document.createElement('div'); card.className = 'tip-card';
    const accepts = (m.accepts || []).map(a => `<span class="tip-chip">${a}</span>`).join('');
    card.innerHTML = `
      <div class="tip-card-head"><span class="tip-coin">${m.label}</span><span class="tip-sym">${m.symbol || ''}</span></div>
      <img class="tip-qr" src="/static/qr/${m.id}.svg" alt="${m.symbol} address QR">
      <div class="tip-accepts">${accepts}</div>
      <div class="tip-addr-row">
        <code class="tip-addr" title="${m.address}">${m.address}</code>
        <button class="tip-copy" data-addr="${m.address}" data-sym="${m.symbol}">copy</button>
      </div>`;
    card.querySelector('.tip-copy').onclick = async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(b.dataset.addr); }
      catch (_) { const t = document.createElement('textarea'); t.value = b.dataset.addr; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
      b.textContent = 'copied ✓'; b.classList.add('done');
      toast(`${b.dataset.sym} address copied — thank you 🙏`);
      setTimeout(() => { b.textContent = 'copy'; b.classList.remove('done'); }, 1600);
    };
    grid.appendChild(card);
  });
}
function renderBrowser(filter) {
  const grid = $('#browserGrid'); grid.innerHTML = '';
  const f = (filter || '').trim().toLowerCase();
  const cur = editPreset();
  STATE.presets.filter(p => !f || p.name.includes(f) || (p.description || '').toLowerCase().includes(f)).forEach(p => {
    const c = document.createElement('div');
    c.className = 'card' + (p.name === cur ? ' active' : '');
    c.innerHTML = `<div class="nm">${p.name}</div><div class="desc">${p.description || ''}</div>
      <div class="meta"><span class="chip">${p.voice_count} voices</span>${p.has_drone ? '<span class="chip">drone</span>' : ''}<button class="play" title="audition">▶</button></div>`;
    c.onclick = (e) => { if (e.target.closest('.play')) return; assignPreset(p.name); };
    c.querySelector('.play').onclick = (e) => { e.stopPropagation(); api.post('/api/audition', { name: p.name }); toast(`auditioning <span class="g">${p.name}</span>`); };
    grid.appendChild(c);
  });
}
async function assignPreset(name) {
  if (FOCUS.kind === 'global') {
    await api.post('/api/preset/use', { name });
    STATE.active = name; $('#npName').textContent = name; toast(`default → <span class="g">${name}</span>`);
  } else {
    const s = FOCUS.s;
    await api.post('/api/session/pin', { id: s.id, preset: name });
    s.preset = name; s.pinned = name; s.source = 'pin'; toast(`<span class="g">${s.base}</span> → ${name}`);
  }
  closeBrowser(); renderRail(); loadFocus();
}

/* ---------------- voices (Mix) ---------------- */
const DELAYS = [
  { v: null, label: 'dry' }, { v: { ms: 220, feedback: 0.20, count: 2 }, label: 'subtle' },
  { v: { ms: 320, feedback: 0.30, count: 3 }, label: 'med' }, { v: { ms: 500, feedback: 0.40, count: 4 }, label: 'long' },
  { v: { ms: 700, feedback: 0.50, count: 5 }, label: 'echo' },
];
function delayIdx(d) { if (!d) return 0; let b = 0; DELAYS.forEach((o, i) => { if (o.v && o.v.ms === d.ms && o.v.count === d.count) b = i; }); return b; }

function renderVoices() {
  const wrap = $('#voices'); wrap.innerHTML = '';
  const P = editPreset();
  DETAIL.voices.forEach((v, i) => {
    const row = document.createElement('div'); row.className = 'vrow'; row.dataset.voice = v.name;
    const col = PALETTE[i % PALETTE.length]; const di = delayIdx(v.delay);
    row.innerHTML = `
      <span class="orb" style="--c:${col}"></span>
      <span class="vname" title="play ${v.name}">${v.name}</span>
      <div class="controls">
        <div class="ctl"><span class="k">vol</span><input type="range" class="r gain" min="0" max="1" step="0.01" value="${v.gain}"><span class="val gv">${fmt(v.gain,2)}</span></div>
        <div class="ctl"><span class="k">rev</span><input type="range" class="r rev wet" min="0" max="1" step="0.01" value="${v.reverb.wet||0}"><span class="val wv">${fmt(v.reverb.wet||0,2)}</span></div>
        <div class="ctl"><span class="k">rate</span><input class="numbox mioi" type="number" min="0.02" max="60" step="0.05" value="${v.mioi}" title="min seconds between fires"><span class="k">s</span></div>
        <div class="ctl"><span class="k">echo</span><div class="chips">${DELAYS.map((o,j)=>`<button class="dchip${j===di?' on':''}" data-j="${j}">${o.label}</button>`).join('')}</div></div>
        <div class="ctl"><span class="k">jit</span><div class="toggle mini${v.rate_jitter?' on':''}" title="pitch jitter"><span class="tk"></span></div></div>
      </div>
      <button class="vplay" title="play">▶</button>`;
    const g = row.querySelector('.gain'); setFill(g);
    g.oninput = () => { row.querySelector('.gv').textContent = fmt(g.value,2); setFill(g); };
    g.onchange = () => api.post('/api/voice', { preset: P, voice: v.name, field: 'gain', value: +g.value });
    const w = row.querySelector('.wet'); setFill(w);
    w.oninput = () => { row.querySelector('.wv').textContent = fmt(w.value,2); setFill(w); };
    w.onchange = () => { if (!v.regenable) { toast('reverb fixed (no renderer)'); return; } api.post('/api/voice/reverb', { preset: P, voice: v.name, wet: +w.value }); toast(`re-rendering <span class="g">${v.name}</span>…`); };
    row.querySelectorAll('.dchip').forEach(ch => ch.onclick = () => { const o = DELAYS[+ch.dataset.j];
      row.querySelectorAll('.dchip').forEach(x => x.classList.remove('on')); ch.classList.add('on');
      if (!o.v) api.post('/api/voice/delay', { preset: P, voice: v.name, off: true });
      else api.post('/api/voice/delay', { preset: P, voice: v.name, ms: o.v.ms, feedback: o.v.feedback, count: o.v.count }); });
    const mi = row.querySelector('.mioi'); mi.onchange = () => api.post('/api/voice', { preset: P, voice: v.name, field: 'mioi', value: +mi.value });
    const jt = row.querySelector('.toggle'); jt.onclick = () => { const on = !jt.classList.contains('on'); jt.classList.toggle('on', on); api.post('/api/voice', { preset: P, voice: v.name, field: 'rate_jitter', value: on }); };
    const play = () => { api.post('/api/voice/play', { preset: P, voice: v.name }); flare(v.name); };
    row.querySelector('.vplay').onclick = play; row.querySelector('.vname').onclick = play;
    wrap.appendChild(row);
  });
}

function renderEvents() {
  const wrap = $('#events'); wrap.innerHTML = ''; const P = editPreset();
  const opts = (sel) => ['<option value="__none__"' + (sel == null ? ' selected' : '') + '>— silent —</option>']
    .concat(DETAIL.voice_names.map(n => `<option ${n === sel ? 'selected' : ''}>${n}</option>`)).join('');
  DETAIL.events.forEach(ev => {
    const row = document.createElement('div'); row.className = 'erow'; row.dataset.event = ev.event;
    row.innerHTML = `<span class="edot"></span><span><span class="ename">${ev.event}</span></span><select class="vsel ${ev.default==null?'silent':''}">${opts(ev.default)}</select>`;
    row.querySelector('select').onchange = e => { api.post('/api/map', { preset: P, event: ev.event, key: 'default', voice: e.target.value }); e.target.classList.toggle('silent', e.target.value === '__none__'); };
    wrap.appendChild(row);
    Object.entries(ev.by_tool).forEach(([tool, voice]) => {
      const sr = document.createElement('div'); sr.className = 'erow sub';
      sr.innerHTML = `<span></span><span><span class="ename">${tool}</span><span class="ekey">by-tool</span></span><select class="vsel ${voice==null?'silent':''}">${opts(voice)}</select>`;
      sr.querySelector('select').onchange = e => api.post('/api/map', { preset: P, event: ev.event, key: tool, voice: e.target.value });
      wrap.appendChild(sr);
    });
    if (ev.on_failure !== null && ev.on_failure !== undefined) {
      const ofv = ev.on_failure === '__none__' ? null : ev.on_failure;
      const sr = document.createElement('div'); sr.className = 'erow sub';
      sr.innerHTML = `<span></span><span><span class="ename">on failure</span><span class="ekey">fallback</span></span><select class="vsel ${ofv==null?'silent':''}">${opts(ofv)}</select>`;
      sr.querySelector('select').onchange = e => api.post('/api/map', { preset: P, event: ev.event, key: 'on_failure', voice: e.target.value });
      wrap.appendChild(sr);
    }
  });
}

function renderSettings() {
  const wrap = $('#settings'); wrap.innerHTML = '';
  const rows = [
    { lab: 'Master gain', small: 'overall volume', val: STATE.master_gain, change: v => { api.post('/api/master', { gain: v }); STATE.master_gain = v; $('#master').value = v; setFill($('#master')); $('#masterVal').textContent = fmt(v,2); } },
    { lab: 'Drone gain', small: 'sustained bed (presets with a drone)', val: STATE.drone_gain, change: v => api.post('/api/drone', { gain: v }) },
  ];
  rows.forEach(r => {
    const el = document.createElement('div'); el.className = 'setrow';
    el.innerHTML = `<div class="lab">${r.lab}<small>${r.small}</small></div><div style="display:flex;align-items:center;gap:10px">
      <input type="range" class="r" min="0" max="1" step="0.01" value="${r.val}" style="width:130px"><span class="val" style="font:11px/1 var(--mono);color:var(--muted);min-width:34px">${fmt(r.val,2)}</span></div>`;
    const inp = el.querySelector('input'); setFill(inp);
    inp.oninput = () => { el.querySelector('.val').textContent = fmt(inp.value,2); setFill(inp); };
    inp.onchange = () => r.change(+inp.value); wrap.appendChild(el);
  });
  const rev = document.createElement('div'); rev.className = 'setrow';
  rev.innerHTML = `<div class="lab">Reverb space<small>hall size · re-renders preset</small></div><div style="display:flex;align-items:center;gap:10px">
    <input type="range" class="r rev" min="0" max="2" step="0.05" value="${DETAIL.reverb_scale ?? 1}" style="width:130px"><span class="val" style="font:11px/1 var(--mono);color:var(--muted);min-width:34px">${fmt(DETAIL.reverb_scale ?? 1,2)}×</span></div>`;
  const ri = rev.querySelector('input'); setFill(ri);
  ri.oninput = () => { rev.querySelector('.val').textContent = fmt(ri.value,2)+'×'; setFill(ri); };
  ri.onchange = () => { api.post('/api/reverb_scale', { preset: editPreset(), value: +ri.value }); $('#reverbScale').value = ri.value; setFill($('#reverbScale')); $('#reverbScaleVal').textContent = fmt(ri.value,2)+'×'; toast('re-rendering reverb space…'); };
  wrap.appendChild(rev);
}

/* ---------------- music (global defaults) ---------------- */
function renderMusic() {
  const m = STATE.music || {}; const sw = $('#musicScale'); sw.innerHTML = '';
  const f = document.createElement('div'); f.className = 'field';
  f.innerHTML = `<div class="flab">Global scale<small>re-keys everything when no session override</small></div>
    <div class="fctl"><select class="vsel" id="scaleSel"><option value="__none__"${!m.scale_global?' selected':''}>preset default</option>
      ${(m.scales||[]).map(s => `<option ${s===m.scale_global?'selected':''}>${s}</option>`).join('')}</select></div>`;
  f.querySelector('#scaleSel').onchange = e => { api.post('/api/scale', { name: e.target.value === '__none__' ? null : e.target.value }); toast(`global scale → ${e.target.value === '__none__' ? 'preset default' : e.target.value}`); };
  sw.appendChild(f);

  const rw = $('#musicRhythm'); rw.innerHTML = '';
  const q = m.quant || { enabled: false, bpm: 120, grid: 0.5 };
  const qf = document.createElement('div'); qf.className = 'field';
  qf.innerHTML = `<div class="flab">Quantize<small>snap triggers to a tempo grid</small></div><div class="fctl">
    <input class="numbox" id="qbpm" type="number" min="30" max="300" step="1" value="${q.bpm}" title="bpm">
    <select class="vsel" id="qgrid" style="min-width:90px">${[[0.25,'16th'],[0.5,'8th'],[1,'quarter'],[2,'half']].map(([v,l])=>`<option value="${v}" ${+q.grid===v?'selected':''}>${l}</option>`).join('')}</select>
    <div class="toggle${q.enabled?' on':''}" id="qtoggle"><span class="tk"></span></div></div>`;
  qf.querySelector('#qtoggle').onclick = (e) => { const on = !e.currentTarget.classList.contains('on'); e.currentTarget.classList.toggle('on', on); api.post('/api/quant', { enabled: on }); toast(`quantize ${on?'on':'off'}`); };
  qf.querySelector('#qbpm').onchange = e => api.post('/api/quant', { bpm: +e.target.value });
  qf.querySelector('#qgrid').onchange = e => api.post('/api/quant', { grid: +e.target.value });
  rw.appendChild(qf);
  const sf = document.createElement('div'); sf.className = 'field';
  if ((m.songs || []).length) {
    sf.innerHTML = `<div class="flab">Global MIDI song<small>drive melodies from a MIDI file</small></div><div class="fctl">
      <select class="vsel" id="songSel" style="min-width:150px"><option value="__none__"${!m.song_global?' selected':''}>— off (markov) —</option>
      ${m.songs.map(s => `<option ${s===m.song_global?'selected':''}>${s}</option>`).join('')}</select></div>`;
    sf.querySelector('#songSel').onchange = e => api.post('/api/song', { name: e.target.value === '__none__' ? null : e.target.value });
  } else sf.innerHTML = `<div class="flab">MIDI song<small>no songs imported — <code>claudio song import</code></small></div>`;
  rw.appendChild(sf);
}

/* ---------------- preset actions ---------------- */
function renderActions() {
  const w = $('#presetActions'); w.innerHTML = ''; const P = editPreset();
  const acts = [
    { lab: 'Audition', small: 'play a taste of this preset', btn: '▶ play', cls: '', fn: () => { api.post('/api/audition', { name: P }); toast(`auditioning <span class="g">${P}</span>`); } },
    { lab: 'Test events', small: 'fire one of each hook event', btn: 'run test', cls: 'ghost', fn: () => { api.post('/api/test', { name: P }); toast('walking through events…'); } },
    { lab: 'Regenerate samples', small: 'rebuild all WAVs from render.py', btn: 'regenerate', cls: 'ghost', fn: () => { api.post('/api/regen', { name: P }); toast(`re-rendering <span class="g">${P}</span>…`); } },
    { lab: 'Reset preset', small: 'restore preset.json from shipped default', btn: 'reset', cls: 'danger', fn: async () => { const r = await api.post('/api/preset/reset', { name: P }); toast(r.ok ? 'reset to default' : (r.msg || 'no default')); if (r.ok) loadFocus(); } },
  ];
  acts.forEach(a => { const el = document.createElement('div'); el.className = 'actrow';
    el.innerHTML = `<div class="alab">${a.lab}<small>${a.small}</small></div><button class="btn ${a.cls}">${a.btn}</button>`;
    el.querySelector('button').onclick = a.fn; w.appendChild(el); });
}

/* ---------------- rules ---------------- */
function presetOptions(sel, includeNone, noneLabel) {
  const o = includeNone ? [`<option value="__none__"${sel==null?' selected':''}>${noneLabel||'— none —'}</option>`] : [];
  return o.concat(STATE.presets.map(p => `<option ${p.name===sel?'selected':''}>${p.name}</option>`)).join('');
}
function renderRules() {
  const rl = $('#rulesList'); rl.innerHTML = '';
  const rules = STATE.rules || [];
  $('#routeBadge').textContent = rules.length ? String(rules.length) : '';
  if (!rules.length) rl.innerHTML = '<div class="rempty">no directory rules — add one below</div>';
  rules.forEach(r => {
    const el = document.createElement('div'); el.className = 'rule';
    const cond = [r.time && `⏰ ${r.time}`, r.idle_after_s && `idle ${r.idle_after_s}s`].filter(Boolean).map(c => `<span class="rcond">${c}</span>`).join('');
    el.innerHTML = `<span class="rpat">${r.pattern}</span><span class="rarrow">→</span><span class="rpre">${r.preset}</span>${cond}<button class="rx" title="remove">✕</button>`;
    el.querySelector('.rx').onclick = async () => { await api.post('/api/rule/rm', { pattern: r.pattern }); await refreshState(); renderRules(); toast('rule removed'); };
    rl.appendChild(el);
  });
  const rp = $('#rulePreset'); if (rp) rp.innerHTML = presetOptions(editPreset(), false);
  const addBtn = $('#ruleAdd');
  if (addBtn && !addBtn.dataset.wired) { addBtn.dataset.wired = '1';
    addBtn.onclick = async () => { const pattern = $('#rulePattern').value.trim(); const preset = $('#rulePreset').value;
      if (!pattern) { toast('enter a path pattern'); return; }
      const r = await api.post('/api/rule/add', { pattern, preset });
      if (r.ok) { $('#rulePattern').value = ''; await refreshState(); renderRules(); toast(`rule added → <span class="g">${preset}</span>`); } else toast('could not add rule'); };
  }
}
async function refreshState() { const s = await api.get('/api/state'); STATE.rules = s.rules; STATE.sessions = s.sessions; STATE.music = s.music; }

/* ---------------- global controls ---------------- */
function setTab(name) { $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name)); $$('.tabpanel').forEach(p => p.hidden = (p.dataset.panel !== name)); }
function wireGlobal() {
  $$('.tab').forEach(t => t.onclick = () => setTab(t.dataset.tab));
  $('#browseBtn').onclick = openBrowser;
  $('#browserClose').onclick = closeBrowser;
  $('#browser').onclick = (e) => { if (e.target.id === 'browser') closeBrowser(); };
  $('#browserSearch').oninput = e => renderBrowser(e.target.value);
  $('#tipBtn').onclick = openTip;
  $('#tipClose').onclick = closeTip;
  $('#tipModal').onclick = (e) => { if (e.target.id === 'tipModal') closeTip(); };
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!$('#browser').hidden) closeBrowser();
    else if (!$('#tipModal').hidden) closeTip();
  });
  const m = $('#master');
  m.oninput = () => { $('#masterVal').textContent = fmt(m.value,2); setFill(m); };
  m.onchange = () => { api.post('/api/master', { gain: +m.value }); STATE.master_gain = +m.value; };
  $('#power').onclick = async () => { const willMute = $('#power').classList.contains('on'); setPower(!willMute); document.body.classList.toggle('is-muted', willMute);
    await api.post('/api/mute', { muted: willMute }); STATE.muted = willMute; toast(willMute ? 'claudio <span class="g">muted</span>' : 'claudio <span class="g">live</span>'); };
  const rs = $('#reverbScale');
  rs.oninput = () => { $('#reverbScaleVal').textContent = fmt(rs.value,2)+'×'; setFill(rs); };
  rs.onchange = () => { api.post('/api/reverb_scale', { preset: editPreset(), value: +rs.value }); toast('re-rendering reverb space…'); };
}
async function syncExternal() {
  const s = await api.get('/api/state');
  STATE.presets = s.presets; STATE.rules = s.rules; STATE.music = s.music;
  if (s.active !== STATE.active) { STATE.active = s.active; $('#npName').textContent = s.active; }
  if (s.muted !== STATE.muted) { STATE.muted = s.muted; setPower(!s.muted); document.body.classList.toggle('is-muted', s.muted); }
  STATE.sessions = s.sessions;
}

/* ---------------- live activity ---------------- */
function startActivityLoop() { poll(); setInterval(poll, 280); }
async function poll() {
  const P = editPreset(); if (!P) return;
  let a; try { a = await api.get('/api/activity?name=' + encodeURIComponent(P)); } catch { return; }
  const live = a.heartbeat && (a.now - a.heartbeat < 12) && !a.muted;
  $('#pulse').classList.toggle('live', !!live);
  $('#pulseTxt').textContent = a.muted ? 'muted' : (live ? 'listening' : 'idle');
  Object.entries(a.voices).forEach(([vn, ts]) => { if (ts && ts !== lastVoiceTs[vn]) { if (lastVoiceTs[vn] !== undefined) flare(vn); lastVoiceTs[vn] = ts; } paintDot($(`.vrow[data-voice="${cssEsc(vn)}"] .orb`), a.now - ts, true); });
  Object.entries(a.events).forEach(([en, ts]) => { paintDot($(`.erow[data-event="${cssEsc(en)}"] .edot`), a.now - ts, false); });
  // refresh rail when sessions change (not whole stage)
  if (a.sessions) {
    STATE.sessions = a.sessions;
    if (a.active && a.active !== STATE.active) { STATE.active = a.active; $('#npName').textContent = a.active; }
    const sig = JSON.stringify(a.sessions.map(s => [s.id, s.preset, s.pinned, s.scale, s.song, s.ended, Math.floor(s.age / 30)])) + '|' + STATE.active + '|' + FOCUS.kind + (FOCUS.id || '');
    const editing = document.activeElement && document.activeElement.closest && document.activeElement.closest('.session-strip,.browser');
    if (sig !== STATE._sig && !editing) { STATE._sig = sig; renderRail(); }
  }
}
function paintDot(el, age, isVoice) {
  if (!el) return;
  if (age == null || age > 1e8 || !isFinite(age)) { el.style.background = 'var(--faint)'; el.style.boxShadow = 'none'; return; }
  const c = isVoice ? (el.style.getPropertyValue('--c') || 'var(--sage)') : 'var(--sage)';
  if (age < 0.6) { el.style.background = c; el.style.boxShadow = `0 0 10px ${c}`; el.style.opacity = 1; }
  else if (age < 1.6) { el.style.background = c; el.style.opacity = .8; el.style.boxShadow = `0 0 5px ${c}`; }
  else if (age < 3.2) { el.style.background = c; el.style.opacity = .45; el.style.boxShadow = 'none'; }
  else { el.style.background = 'var(--faint)'; el.style.opacity = 1; el.style.boxShadow = 'none'; }
}

/* ---------------- constellation canvas ---------------- */
const sky = $('#sky'); const ctx = sky.getContext('2d');
let nodes = [], rings = [], DPR = Math.min(2, window.devicePixelRatio || 1);
function resizeSky() { const r = sky.parentElement.getBoundingClientRect(); sky.width = r.width * DPR; sky.height = r.height * DPR; sky.style.width = r.width + 'px'; sky.style.height = r.height + 'px'; layoutNodes(); }
window.addEventListener('resize', resizeSky);
function buildNodes() { nodes = (DETAIL ? DETAIL.voices : []).map((v, i) => ({ name: v.name, gain: v.gain, col: PALETTE[i % PALETTE.length], fire: 0, x: 0, y: 0, rad: 0, ang: 0, phase: i * 1.7, base: 5 + Math.min(11, (v.gain || .4) * 13) })); resizeSky(); }
let SKY = { cx: 0, cy: 0, sx: 1, sy: 1 };
function layoutNodes() { const W = sky.width, H = sky.height; SKY.cx = W / 2; SKY.cy = H * 0.5; const GA = Math.PI * (3 - Math.sqrt(5)); const maxR = Math.sqrt(Math.max(1, nodes.length - 1) + 0.6); SKY.sx = (W * 0.40) / maxR; SKY.sy = (H * 0.34) / maxR; nodes.forEach((n, i) => { n.rad = Math.sqrt(i + 0.6); n.ang = i * GA - Math.PI / 2; }); }
function flare(name) { const n = nodes.find(x => x.name === name); if (!n) return; n.fire = 1; rings.push({ x: n.x, y: n.y, t: performance.now(), col: n.col, r0: n.base * DPR }); }
sky.addEventListener('click', (e) => { const r = sky.getBoundingClientRect(); const mx = (e.clientX - r.left) * DPR, my = (e.clientY - r.top) * DPR; let hit = null, hd = 1e9; nodes.forEach(n => { const d = Math.hypot(n.x - mx, n.y - my); if (d < hd) { hd = d; hit = n; } }); if (hit && hd < 26 * DPR) { api.post('/api/voice/play', { preset: editPreset(), voice: hit.name }); flare(hit.name); toast(`<span class="g">${hit.name}</span>`); } });
let hover = null;
sky.addEventListener('mousemove', (e) => { const r = sky.getBoundingClientRect(); const mx = (e.clientX - r.left) * DPR, my = (e.clientY - r.top) * DPR; let hit = null, hd = 1e9; nodes.forEach(n => { const d = Math.hypot(n.x - mx, n.y - my); if (d < hd) { hd = d; hit = n; } }); hover = (hit && hd < 26 * DPR) ? hit : null; sky.style.cursor = hover ? 'pointer' : 'default'; });
function startSky() { resizeSky(); requestAnimationFrame(drawSky); }
function drawSky(t) {
  ctx.clearRect(0, 0, sky.width, sky.height);
  const breath = 0.5 + 0.5 * Math.sin(t / 2600); const s = t * 0.001; const amp = 13 * DPR;
  nodes.forEach(n => { const hx = SKY.cx + SKY.sx * n.rad * Math.cos(n.ang); const hy = SKY.cy + SKY.sy * n.rad * Math.sin(n.ang); const a = n.phase;
    const dx = Math.sin(s * 0.43 + a) + 0.55 * Math.sin(s * 0.91 + a * 1.7) + 0.3 * Math.sin(s * 1.7 + a * 2.6);
    const dy = Math.cos(s * 0.39 + a * 1.3) + 0.55 * Math.cos(s * 0.83 + a * 2.1) + 0.3 * Math.cos(s * 1.5 + a * 1.4);
    n.x = hx + dx * amp * 0.55; n.y = hy + dy * amp * 0.55; });
  const WEB = 96 * DPR; ctx.lineWidth = DPR;
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) { const a = nodes[i], b = nodes[j], d = Math.hypot(a.x - b.x, a.y - b.y); if (d < WEB) { ctx.strokeStyle = `rgba(244,225,193,${0.055 * (1 - d / WEB)})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); } }
  const now = performance.now(); rings = rings.filter(r => now - r.t < 1300);
  rings.forEach(r => { const p = (now - r.t) / 1300, rad = r.r0 + p * 60 * DPR; ctx.strokeStyle = hexA(r.col, (1 - p) * 0.55); ctx.lineWidth = (1 - p) * 2.4 * DPR + 0.3; ctx.beginPath(); ctx.arc(r.x, r.y, rad, 0, 7); ctx.stroke(); });
  nodes.forEach(n => { n.fire *= 0.92; const twinkle = 0.78 + 0.22 * Math.sin(t * 0.0011 + n.phase * 2.3); const glowPulse = (0.5 + 0.4 * breath) * twinkle; const rr = (n.base + n.fire * 6) * DPR; const isH = hover === n;
    const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, rr * 4.2); g.addColorStop(0, hexA(n.col, (0.5 + n.fire * 0.5) * (isH ? 1 : glowPulse))); g.addColorStop(1, hexA(n.col, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(n.x, n.y, rr * 4.2, 0, 7); ctx.fill();
    ctx.fillStyle = hexA(n.col, 0.55 + n.fire * 0.45 + (isH ? .2 : 0)); ctx.beginPath(); ctx.arc(n.x, n.y, rr, 0, 7); ctx.fill();
    ctx.fillStyle = hexA('#fff7e6', 0.5 + n.fire * 0.5); ctx.beginPath(); ctx.arc(n.x, n.y, rr * 0.42, 0, 7); ctx.fill();
    if (isH || n.fire > 0.25) { ctx.font = `${10 * DPR}px 'Space Mono', monospace`; ctx.fillStyle = hexA('#f5ecdd', isH ? 0.95 : n.fire); ctx.textAlign = 'center'; ctx.fillText(n.name, n.x, n.y + rr + 14 * DPR); } });
  requestAnimationFrame(drawSky);
}
function hexA(hex, a) { if (hex[0] !== '#') return hex; const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(1, a))})`; }

boot();
