// N8 ZelloBridge – dashboard (design: DMR Die N8schicht)
'use strict';
const $ = id => document.getElementById(id);
const S = { tab: 'live', me: null, info: {}, live: null, hw: null, lhFilter: 'all', admin: { tab: 'services' },
            stats: { range: '24h', metric: 'airtime', data: null }, hwRange: '24h', es: null, esOk: false };
const C = { rf: '#ff9f0a', zello: '#30d158', cpu: '#5ac8fa', bridge: '#bf5af2', temp: '#ff9f0a', mem: '#5e5ce6', rx: '#30d158', tx: '#5ac8fa' };

// ---------------------------------------------------------------- helpers
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
async function api(method, url, body) {
    const opt = { method, credentials: 'same-origin', headers: { 'X-N8': '1' } };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const r = await fetch(url, opt);
    let d = {}; try { d = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
}
function fmtDur(s, short) {
    s = Math.round(s || 0);
    if (s < 60) return `${s} s`;
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return short ? `${d} d ${h} h` : `${d} d ${h} h ${m} m`;
    if (h) return `${h} h ${m} m`;
    return short ? `${m} m` : `${m} m ${s % 60} s`;
}
function fmtClock(sec) { sec = Math.max(0, Math.floor(sec)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
function fmtDate(ts) { return ts ? new Date(ts * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–'; }
function ago(ts) { if (!ts) return '–'; const d = Date.now() / 1000 - ts; return d < 60 ? 'just now' : d < 3600 ? `${Math.floor(d / 60)} min ago` : d < 86400 ? `${Math.floor(d / 3600)} h ago` : `${Math.floor(d / 86400)} d ago`; }
function fmtBytes(b) { if (b == null) return '–'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`; }
function fmtRate(b) { if (b == null) return '–'; const k = b * 8 / 1000; return k < 1000 ? `${k.toFixed(k < 10 ? 1 : 0)} kbit/s` : `${(k / 1000).toFixed(1)} Mbit/s`; }
function num(n) { return Number(n || 0).toLocaleString('de-DE'); }
function toast(msg, bad) {
    const t = document.createElement('div');
    t.className = 'chip ' + (bad ? 'bad' : 'ok');
    t.style.cssText = 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:4000;box-shadow:0 10px 30px rgba(0,0,0,.4);max-width:90vw;white-space:normal';
    t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), bad ? 6000 : 3000);
}
function initials(n) { return esc(String(n || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?'); }
function hashStr(s) { let h = 0; for (const ch of String(s || '')) h = (h * 31 + ch.charCodeAt(0)) | 0; return Math.abs(h); }
function avatar(name, cls) { const h = hashStr(name) * 47 % 360; return `<span class="avatar ${cls || ''}" style="background:linear-gradient(135deg,hsl(${h} 70% 50%),hsl(${(h + 40) % 360} 75% 42%))">${initials(name)}</span>`; }
const DIR = { rf: ['📻', 'Radio → Zello', 'Radio'], zello: ['💬', 'Zello → Radio', 'Zello'] };
function who(t) { return t.dir === 'rf' ? 'Radio (USRP)' : (t.user || 'Zello'); }

// ---------------------------------------------------------------- boot & header
async function boot() {
    try {
        S.info = await api('GET', '/api/info'); S.me = S.info.user || null;
        renderHeader();
        if (S.info.login_required) { openLogin(true); return; }
        S.live = await api('GET', '/api/live'); S.hw = S.live.hw;
        renderLive(); connectSSE();
        const h = location.hash.replace('#', '');
        if (h) { const [t, a] = h.split('/'); if (['stats', 'hw'].includes(t)) showTab(t); else if (t === 'admin' && S.me) showTab('admin', a); }
    } catch (e) { console.error(e); setTimeout(boot, 3000); }
}
function renderHeader() {
    const i = S.info;
    $('title').textContent = i.title || 'N8 ZelloBridge'; document.title = i.title || 'N8 ZelloBridge';
    $('subtitle').textContent = [i.subtitle, i.demo ? 'Demo' : ''].filter(Boolean).join(' · ');
    $('ver').textContent = 'v' + (i.version || '');
    $('tabAdminBtn').classList.toggle('hidden', !S.me);
    $('setupBanner').classList.toggle('hidden', !i.setup_needed);
    $('userArea').innerHTML = S.me ? `<button class="btn" onclick="userMenu(event)">👤 ${esc(S.me.display_name || S.me.username)} ▾</button>`
        : '<button class="btn primary" onclick="openLogin()">🔐 Login</button>';
}
function userMenu(ev) {
    ev.stopPropagation();
    const r = ev.target.getBoundingClientRect(), m = document.createElement('div');
    m.className = 'glass';
    m.style.cssText = `position:fixed;top:${r.bottom + 8}px;right:${Math.max(8, innerWidth - r.right)}px;z-index:300;border-radius:16px;padding:6px;width:210px;background:rgba(22,28,48,.95)`;
    const b = (l, f) => `<button class="btn" style="width:100%;border-radius:10px;margin:2px 0" onclick="${f}">${l}</button>`;
    m.innerHTML = `<div style="padding:8px 12px" class="dim">${esc(S.me.username)} · ${esc(S.me.role)}</div>${b('⚙️ Admin', "showTab('admin')")}${b('🔑 Password', 'openPassword()')}${b('🚪 Log out', 'logout()')}`;
    document.body.appendChild(m);
    setTimeout(() => document.addEventListener('click', () => m.remove(), { once: true }), 0);
}
function modal(html, locked) {
    $('modalRoot').innerHTML = `<div class="overlay" id="ov"><div class="modal">${html}</div></div>`;
    if (!locked) $('ov').addEventListener('click', e => { if (e.target.id === 'ov') closeModal(); });
}
function closeModal() { $('modalRoot').innerHTML = ''; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
function openLogin(locked) {
    modal(`<img class="logo" src="/static/n8-zellobridge-icon.svg" alt=""><h2 style="text-align:center">${esc(S.info.title || 'N8 ZelloBridge')}</h2>
        <p class="dim" style="text-align:center;margin:0 0 12px">${locked ? 'This dashboard is private. Please log in.' : 'Log in to manage the bridge.'}</p>
        <form onsubmit="event.preventDefault(); doLogin(this)">
            <div class="row"><label>Username</label><input type="text" name="username" autocomplete="username" required></div>
            <div class="row"><label>Password</label><input type="password" name="password" autocomplete="current-password" required></div>
            <label style="display:flex;gap:8px;align-items:center;margin-top:6px" class="dim"><input type="checkbox" name="remember"> Stay logged in (30 days)</label>
            <div class="err" id="loginErr"></div>
            <div class="actions">${locked ? '' : '<button type="button" class="btn" onclick="closeModal()">Cancel</button>'}<button class="btn primary">Log in</button></div>
        </form>`, locked);
    setTimeout(() => document.querySelector('#ov input[name=username]')?.focus(), 50);
}
async function doLogin(f) {
    try { await api('POST', '/api/auth/login', { username: f.username.value, password: f.password.value, remember: f.remember.checked }); location.reload(); }
    catch (e) { $('loginErr').textContent = e.message; }
}
async function logout() { await api('POST', '/api/auth/logout', {}); location.hash = ''; location.reload(); }
function openPassword() {
    modal(`<h2>🔑 Password</h2><form onsubmit="event.preventDefault(); savePassword(this)">
        <div class="row"><label>Current password</label><input type="password" name="old" required autocomplete="current-password"></div>
        <div class="row"><label>New password (min. 10)</label><input type="password" name="new" required minlength="10" autocomplete="new-password"></div>
        <div class="row"><label>Repeat</label><input type="password" name="rep" required minlength="10" autocomplete="new-password"></div>
        <div class="err" id="pwErr"></div><div class="actions"><button type="button" class="btn" onclick="closeModal()">Cancel</button><button class="btn primary">Save</button></div></form>`);
}
async function savePassword(f) {
    if (f.new.value !== f.rep.value) { $('pwErr').textContent = 'The new passwords differ.'; return; }
    try { await api('POST', '/api/auth/password', { old: f.old.value, new: f.new.value }); toast('Saved – please log in again'); setTimeout(() => location.reload(), 1200); }
    catch (e) { $('pwErr').textContent = e.message; }
}
function confirmBox(title, body, ok, cls) {
    return new Promise(res => {
        modal(`<h2>${esc(title)}</h2><p class="dim">${body}</p><div class="actions"><button class="btn" id="cNo">Cancel</button><button class="btn ${cls || 'primary'}" id="cYes">${esc(ok)}</button></div>`);
        $('cNo').onclick = () => { closeModal(); res(false); }; $('cYes').onclick = () => { closeModal(); res(true); };
    });
}

// ---------------------------------------------------------------- tabs
function showTab(tab, sub) {
    S.tab = tab;
    document.querySelectorAll('#mainTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    ['live', 'stats', 'hw', 'admin'].forEach(t => $('tab-' + t).classList.toggle('hidden', t !== tab));
    if (tab === 'stats') loadStats();
    if (tab === 'hw') { renderCores(); loadHwCharts(); }
    if (tab === 'admin') showAdmin(sub || S.admin.tab);
    history.replaceState(null, '', tab === 'live' ? location.pathname : '#' + tab + (tab === 'admin' ? '/' + (sub || S.admin.tab) : ''));
    hideTip();
}
$('mainTabs').addEventListener('click', e => { const b = e.target.closest('button[data-tab]'); if (b) showTab(b.dataset.tab); });
function bindSeg(id, fn) { $(id).addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; document.querySelectorAll(`#${id} button`).forEach(x => x.classList.toggle('active', x === b)); fn(b.dataset.v); }); }

// ---------------------------------------------------------------- live data (Server-Sent Events)
function connectSSE() {
    const es = new EventSource('/api/events'); S.es = es;
    es.onopen = () => { S.esOk = true; renderChips(); };
    es.onerror = () => { S.esOk = false; renderChips(); };
    es.onmessage = m => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
}
let todayT = null;
function onEvent(ev) {
    const L = S.live; if (!L) return;
    switch (ev.type) {
        case 'hw': S.hw = ev.hw; renderHw(); if (S.tab === 'hw') renderCores(); break;
        case 'services': L.services = ev.services; renderServices(); renderFlow(); renderChips(); renderToday(); break;
        case 'bridge': L.bridge = ev.bridge; renderFlow(); renderChips(); break;
        case 'talk': L.bridge.talk = ev.talk; renderFlow(); renderChips(); break;
        case 'heard':
            L.bridge.talk = null; L.last_heard.unshift(ev.entry); L.last_heard.length = Math.min(L.last_heard.length, 50);
            renderFlow(); renderChips(); renderLH(ev.entry.id);
            clearTimeout(todayT); todayT = setTimeout(async () => { S.live.today = (await api('GET', '/api/live')).today; renderToday(); if (S.tab === 'stats') loadStats(); }, 1200);
            break;
        case 'event': L.events.unshift(ev.event); L.events.length = Math.min(L.events.length, 60); renderFeed(true); break;
    }
}
function bridgeSvc() { return ((S.live && S.live.services) || []).find(s => s.bridge) || {}; }
const ZST = { ready: ['ok', '✓ Zello channel ready'], online: ['ok', '✓ Zello logged in'], connecting: ['warn', '… Zello connecting'],
              offline: ['bad', '✕ Zello offline'], error: ['bad', '✕ Zello error'], stopped: ['bad', '✕ Bridge stopped'], unknown: ['', 'Zello'] };
function renderChips() {
    const set = (id, cls, t) => { const e = $(id); e.className = 'chip ' + cls; e.textContent = t; };
    set('chipLive', S.esOk ? 'ok' : 'bad', S.esOk ? '✓ Live data' : '✕ Live data');
    const sv = bridgeSvc();
    set('chipSvc', sv.active === 'active' ? 'ok' : sv.active ? 'bad' : '', sv.active === 'active' ? '✓ Bridge service' : `✕ Bridge ${sv.active || ''}`);
    const z = ZST[(S.live && S.live.bridge.zello) || 'unknown'] || ZST.unknown;
    set('chipZello', z[0], z[1]);
    const t = S.live && S.live.bridge.talk;
    set('chipAir', t ? 'ok' : '', t ? `🎙️ ${DIR[t.dir][0]} ${who(t)}` : '💤 Quiet');
}
function renderLive() { renderChips(); renderToday(); renderFlow(); renderLH(); renderFeed(); renderHw(); renderServices(); renderCfg(); }
function renderToday() {
    const t = S.live.today || {}, sv = bridgeSvc();
    $('sTalks').textContent = num(t.talks); $('sAir').textContent = fmtDur(t.airtime, true);
    $('sRf').textContent = num(t.rf); $('sZello').textContent = num(t.zello); $('sUsers').textContent = num(t.users);
    $('sUp').textContent = sv.active === 'active' && sv.since ? fmtDur(Date.now() / 1000 - sv.since, true) : '–';
}
function renderFlow() {
    const L = S.live, b = L.bridge, sv = bridgeSvc(), c = L.config || {}, t = b.talk;
    const up = sv.active === 'active', zok = ['ready', 'online'].includes(b.zello);
    const extra = (L.services || []).filter(s => !s.bridge);
    const radioSub = extra.length ? extra.map(s => `${esc(s.unit)} ${s.active === 'active' ? '✓' : '✕'}`).join(' · ') : 'AllStarLink / SvxLink';
    const z = ZST[b.zello] || ZST.unknown;
    $('flow').innerHTML = `
        <div class="node ${t && t.dir === 'rf' ? 'hot' : ''}"><div class="ic">📻</div><div class="t">Radio</div><div class="s">${radioSub}</div>
            <span class="pill ${c.usrp_listening === false ? 'bad' : 'tg'}">USRP ${esc(c.usrp_rx || '?')} ⇄ ${esc(c.usrp_tx || '?')}</span></div>
        <div class="wire ${!up ? 'dead' : t ? (t.dir === 'rf' ? 'right' : 'left') : ''}"></div>
        <div class="node center ${up ? (t ? 'hot' : '') : 'down'}"><div class="ic">🌉</div><div class="t">asl-zello-bridge</div>
            <div class="s">${up ? `running since ${fmtDate(sv.since)}${sv.restarts ? ` · ${sv.restarts} restarts` : ''}` : `service ${esc(sv.active || 'unknown')}`}</div>
            <span class="pill ${up ? 'ok' : 'bad'}">${up ? `PID ${sv.pid} · ${S.hw && S.hw.bridge && S.hw.bridge.cpu != null ? S.hw.bridge.cpu + ' % CPU' : ''}` : 'stopped'}</span></div>
        <div class="wire ${!up || !zok ? 'dead' : t ? (t.dir === 'rf' ? 'right' : 'left') : ''}"></div>
        <div class="node ${t && t.dir === 'zello' ? 'hot' : ''} ${!zok && up ? 'down' : ''}"><div class="ic">💬</div><div class="t">Zello</div>
            <div class="s">${esc(c.channel || 'channel')}${c.mode ? ' · ' + esc(c.mode) : ''}</div><span class="pill ${z[0]}">${esc(z[1].replace(/^[✓✕…] /, ''))}</span></div>`;
    const air = $('air');
    if (t) {
        air.className = 'air active ' + t.dir;
        air.innerHTML = `<span class="pill air">ON AIR</span>${t.dir === 'zello' ? avatar(t.user || 'Zello') : '<span class="avatar" style="background:linear-gradient(135deg,#ff9f0a,#ff453a)">📻</span>'}
            <div><div class="call">${esc(who(t))}</div><div class="dim">${DIR[t.dir][0]} ${DIR[t.dir][1]}</div></div><div class="timer" data-start="${t.ts}">${fmtClock(Date.now() / 1000 - t.ts)}</div>`;
    } else {
        const last = S.live.last_heard[0];
        air.className = 'air';
        air.innerHTML = `<span class="pill">Quiet</span><span class="idle">${last ? `Last: <b style="color:var(--text)">${esc(who(last))}</b> · ${DIR[last.dir][1]} · ${fmtDur(last.duration)} · ${ago(last.end_ts || last.ts)}` : 'Nobody is talking right now'}</span>`;
    }
}
setInterval(() => document.querySelectorAll('.timer[data-start]').forEach(t => t.textContent = fmtClock(Date.now() / 1000 - Number(t.dataset.start))), 1000);
bindSeg('lhFilter', v => { S.lhFilter = v; renderLH(); });
function renderLH(newId) {
    let rows = S.live.last_heard || [];
    if (S.lhFilter !== 'all') rows = rows.filter(r => r.dir === S.lhFilter);
    rows = rows.slice(0, 15);
    $('lhBody').innerHTML = rows.length ? rows.map(r => `<tr class="${r.id === newId ? 'new' : ''}"><td>${fmtTime(r.ts)}</td>
        <td><span class="pill ${r.dir === 'rf' ? 'warn' : 'ok'}">${DIR[r.dir][0]} ${DIR[r.dir][1]}</span></td>
        <td><span class="who">${r.dir === 'zello' ? avatar(r.user || 'Zello', 'sm') : '<span class="avatar sm" style="background:linear-gradient(135deg,#ff9f0a,#ff453a)">📻</span>'}<b>${esc(who(r))}</b></span></td>
        <td class="num">${fmtDur(r.duration)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty">Nothing heard yet.</td></tr>';
}
const EVI = { login: '🔓', ready: '✅', disconnect: '⚠️', woodpecker: '🐦', start: '▶️', service: '⚙️', error: '⛔', warning: '⚠️', stats: '📊' };
function renderFeed(flash) {
    const ev = S.live.events || [];
    $('feed').innerHTML = ev.length ? ev.slice(0, 40).map((e, i) => `<div class="ev ${e.level} ${flash && i === 0 ? 'new' : ''}"><span class="t">${fmtDate(e.ts)}</span><span>${EVI[e.kind] || '•'}</span><span class="x">${esc(e.text)}</span></div>`).join('')
        : '<div class="empty">No events yet.</div>';
}
function gauge(v, max, label, txt, color) {
    const p = Math.max(0, Math.min(1, (v || 0) / max)), r = 40, c = 2 * Math.PI * r * 0.75;
    const col = color || (p > 0.85 ? '#ff453a' : p > 0.65 ? '#ffd60a' : '#30d158');
    return `<div class="gauge"><svg viewBox="0 0 100 92"><g transform="rotate(135 50 50)">
        <circle cx="50" cy="50" r="${r}" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="9" stroke-linecap="round" stroke-dasharray="${c} 999"/>
        <circle cx="50" cy="50" r="${r}" fill="none" stroke="${col}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${c * p} 999" style="transition:stroke-dasharray .6s"/></g>
        <text x="50" y="56" text-anchor="middle" fill="#f5f7fb" font-size="17" font-weight="700">${txt}</text></svg><div class="gk">${label}</div></div>`;
}
function renderHw() {
    const h = S.hw, st = S.live.host || {};
    if (!h) return;
    const mem = h.mem_total ? h.mem_used / h.mem_total * 100 : 0, disk = h.disk_total ? h.disk_used / h.disk_total * 100 : 0;
    $('gauges').innerHTML = gauge(h.cpu, 100, 'CPU', `${Math.round(h.cpu)}%`) + gauge(h.temp, 90, 'Temperature', h.temp != null ? `${Math.round(h.temp)}°` : '–', h.temp > 75 ? '#ff453a' : h.temp > 62 ? '#ffd60a' : '#5ac8fa')
        + gauge(mem, 100, 'Memory', `${Math.round(mem)}%`) + gauge(disk, 100, 'SD card', `${Math.round(disk)}%`);
    $('hwKv').innerHTML = `<div class="k">Board</div><div class="v">${esc(st.model || '–')}</div>
        <div class="k">Clock</div><div class="v">${h.freq ? h.freq + ' MHz' : '–'}${st.freq_max ? ` <span class="dim">of ${st.freq_max}</span>` : ''} · load ${h.load.join(' · ')}</div>
        <div class="k">Memory</div><div class="v">${fmtBytes(h.mem_used)} of ${fmtBytes(h.mem_total)}</div>
        <div class="k">Network ${esc(h.iface || '')}</div><div class="v">↓ ${fmtRate(h.rx)} · ↑ ${fmtRate(h.tx)}</div>
        <div class="k">Bridge process</div><div class="v">${h.bridge && h.bridge.pid ? `${h.bridge.cpu ?? '–'} % CPU · ${fmtBytes(h.bridge.rss)}` : '–'}</div>
        <div class="k">Uptime</div><div class="v">${fmtDur(h.uptime, true)}</div>`;
}
function renderServices() {
    const svc = S.live.services || [];
    $('svcList').innerHTML = svc.length ? svc.map(s => `<div class="svc"><span class="dot ${s.active === 'active' ? 'on' : s.active === 'unknown' ? 'unk' : 'off'}"></span>
        <div style="min-width:0;flex:1"><div class="n">${esc(s.unit)} ${s.bridge ? '<span class="pill tg" style="margin-left:4px">bridge</span>' : ''}</div>
        <div class="d">${esc(s.description || '')}</div></div>
        <div class="num"><span class="pill ${s.active === 'active' ? 'ok' : 'bad'}">${esc(s.active)}</span><div class="d">${s.since ? 'since ' + fmtDate(s.since) : ''}${s.memory ? ' · ' + fmtBytes(s.memory) : ''}</div></div></div>`).join('')
        : '<div class="empty">No services found.</div>';
}
function renderCfg() {
    const c = S.live.config || {}, b = S.live.bridge;
    const row = (k, v) => v === undefined || v === '' || v === null ? '' : `<div class="k">${k}</div><div class="v">${v}</div>`;
    $('cfgKv').innerHTML = row('Channel', esc(c.channel)) + row('Account', esc(c.username)) + row('Type', esc(c.mode)) + row('Server', `<span class="mono" style="font-size:12px">${esc(c.endpoint || b.endpoint)}</span>`)
        + row('USRP in / out', `${esc(c.usrp_rx)} / ${esc(c.usrp_tx)} ${c.usrp_listening === true ? '<span class="pill ok">listening</span>' : c.usrp_listening === false ? '<span class="pill bad">not bound</span>' : ''}`)
        + row('Gain RX / TX', c.gain_rx !== '' ? `${esc(c.gain_rx)} dB / ${esc(c.gain_tx)} dB` : '')
        + row('Last login', b.last_login ? fmtDate(b.last_login) : '–')
        + row('Disconnects', b.counters ? `${b.counters.ws_closed} · woodpecker ${b.counters.woodpecker} · errors ${b.counters.errors}` : '')
        + (b.last_error ? row('Last error', `<span style="color:#ff8a80">${esc(b.last_error.text)}</span>`) : '');
}

// ---------------------------------------------------------------- charts
function tip(ev, html) { const t = $('tip'); t.innerHTML = html; t.style.display = 'block'; const r = t.getBoundingClientRect(); let x = ev.clientX + 14, y = ev.clientY + 14; if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14; if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14; t.style.left = x + 'px'; t.style.top = y + 'px'; }
function hideTip() { $('tip').style.display = 'none'; }
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p; return 10 * p; }
const T = {};
function bars(el, labels, series, fmt, tipTitle, width, timeAxis) {
    // series: [{name, color, values}], stacked
    const W = width || 900, H = 230, L = 46, B = 26, n = labels.length, w = (W - L - 8) / Math.max(n, 1);
    const tot = labels.map((_, i) => series.reduce((a, s) => a + s.values[i], 0)), top = Math.max(...tot, 0), mx = timeAxis && top >= 120 ? niceMax(top / 60) * 60 : niceMax(top);
    let g = '';
    for (let k = 0; k <= 4; k++) { const y = 8 + (H - B - 8) * k / 4; g += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="rgba(255,255,255,.06)"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" fill="rgba(235,240,250,.5)" font-size="11">${fmt(mx * (4 - k) / 4, true)}</text>`; }
    const every = n <= (W < 700 ? 8 : 12) ? 1 : n <= 26 ? (W < 700 ? 3 : 2) : n <= 32 ? 4 : Math.ceil(n / 12);
    labels.forEach((l, i) => {
        let y0 = H - B;
        series.forEach(s => { const h = (H - B - 8) * s.values[i] / mx; if (h > 0) { g += `<rect x="${L + i * w + 2}" y="${y0 - h}" width="${Math.max(1, w - 4)}" height="${h}" rx="3" fill="${s.color}"/>`; } y0 -= h; });
        g += `<rect x="${L + i * w}" y="0" width="${w}" height="${H - B}" fill="transparent" data-i="${i}"/>`;
        if (i % every === 0) g += `<text x="${L + i * w + w / 2}" y="${H - 8}" text-anchor="middle" fill="rgba(235,240,250,.5)" font-size="11">${esc(l)}</text>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
    el.querySelector('svg').onmousemove = e => { const i = e.target.dataset && e.target.dataset.i; if (i == null) return hideTip();
        tip(e, `<b>${esc(tipTitle(+i))}</b>${series.map(s => `<div class="r"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><span>${fmt(s.values[i])}</span></div>`).join('')}`); };
    el.querySelector('svg').onmouseleave = hideTip;
}
function lines(el, ts, series, fmt, maxHint) {
    const W = 600, H = 220, L = 52, B = 24, n = ts.length;
    if (!n) { el.innerHTML = '<div class="empty">No values in this period yet.</div>'; return; }
    const mx = niceMax(Math.max(maxHint || 0, ...series.flatMap(s => s.values.filter(v => v != null))));
    const x = i => L + (W - L - 6) * (n === 1 ? 0.5 : i / (n - 1)), y = v => 8 + (H - B - 8) * (1 - v / mx);
    let g = '';
    for (let k = 0; k <= 4; k++) { const yy = 8 + (H - B - 8) * k / 4; g += `<line x1="${L}" x2="${W}" y1="${yy}" y2="${yy}" stroke="rgba(255,255,255,.06)"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end" fill="rgba(235,240,250,.5)" font-size="11">${fmt(mx * (4 - k) / 4, true)}</text>`; }
    const span = ts[n - 1] - ts[0], ticks = 6;
    for (let k = 0; k <= ticks; k++) { const i = Math.round((n - 1) * k / ticks), d = new Date(ts[i] * 1000);
        g += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" fill="rgba(235,240,250,.5)" font-size="11">${span > 2 * 86400 ? d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) : d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}</text>`; }
    series.forEach(s => {
        let d = '', pen = false;
        s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
        if (s.fill) g += `<path d="${d}L${x(n - 1)},${H - B}L${x(0)},${H - B}Z" fill="${s.color}" opacity=".13"/>`;
        g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    });
    g += `<line id="cx" x1="0" x2="0" y1="8" y2="${H - B}" stroke="rgba(255,255,255,.25)" opacity="0"/><rect x="${L}" y="0" width="${W - L}" height="${H - B}" fill="transparent" class="hit"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
    const svg = el.querySelector('svg'), cx = svg.querySelector('#cx');
    svg.onmousemove = e => { const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W; const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - 6) * (n - 1))));
        cx.setAttribute('x1', x(i)); cx.setAttribute('x2', x(i)); cx.setAttribute('opacity', 1);
        tip(e, `<b>${fmtDate(ts[i])}</b>${series.map(s => `<div class="r"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><span>${s.values[i] == null ? '–' : fmt(s.values[i])}</span></div>`).join('')}`); };
    svg.onmouseleave = () => { hideTip(); cx.setAttribute('opacity', 0); };
}

// ---------------------------------------------------------------- statistics
bindSeg('stRange', v => { S.stats.range = v; loadStats(); });
bindSeg('stMetric', v => { S.stats.metric = v; renderStats(); });
async function loadStats() { try { S.stats.data = await api('GET', `/api/stats?range=${S.stats.range}`); renderStats(); } catch (e) { toast(e.message, true); } }
function bucketLabel(d, ts, long) {
    const dt = new Date(ts * 1000);
    if (d.step < 86400) return long ? dt.toLocaleString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : String(dt.getHours()).padStart(2, '0') + ':00';
    return dt.toLocaleDateString('de-DE', long ? { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' } : { day: '2-digit', month: '2-digit' });
}
function renderStats() {
    const d = S.stats.data; if (!d) return;
    const air = S.stats.metric === 'airtime', t = d.totals, rf = t.rf || {}, z = t.zello || {};
    $('kTalks').textContent = num((rf.talks || 0) + (z.talks || 0)); $('kAir').textContent = fmtDur((rf.airtime || 0) + (z.airtime || 0), true);
    $('kRf').textContent = air ? fmtDur(rf.airtime || 0, true) : num(rf.talks); $('kZello').textContent = air ? fmtDur(z.airtime || 0, true) : num(z.talks);
    $('stNote').textContent = d.step < 86400 ? (d.step === 3600 ? 'per hour' : 'per 6 hours') : d.step === 86400 ? 'per day' : 'per week';
    const fmt = air ? (v, axis) => axis ? (v >= 3600 ? +(v / 3600).toFixed(1) + ' h' : v >= 60 ? +(v / 60).toFixed(1) + ' m' : Math.round(v) + ' s') : fmtDur(v) : v => num(Math.round(v));
    bars($('stBars'), d.buckets.map(b => bucketLabel(d, b.ts)), [
        { name: 'Radio → Zello', color: C.rf, values: d.buckets.map(b => air ? b.rf_s : b.rf) },
        { name: 'Zello → Radio', color: C.zello, values: d.buckets.map(b => air ? b.zello_s : b.zello) }], fmt, i => bucketLabel(d, d.buckets[i].ts, true), 900, air);
    bars($('stHours'), d.hours.map((_, h) => String(h).padStart(2, '0')), [{ name: 'Talks', color: C.cpu, values: d.hours }], v => num(Math.round(v)), i => `${String(i).padStart(2, '0')}:00–${String(i).padStart(2, '0')}:59`, 520);
    $('stTop').innerHTML = d.top.length ? d.top.map(u => `<tr><td><span class="who">${avatar(u.user, 'sm')}<b>${esc(u.user)}</b></span></td><td class="num">${num(u.talks)}</td><td class="num">${fmtDur(u.airtime, true)}</td><td class="hide-sm">${ago(u.last)}</td></tr>`).join('')
        : '<tr><td colspan="4" class="empty">No Zello users in this period.</td></tr>';
}

// ---------------------------------------------------------------- hardware
bindSeg('hwRange', v => { S.hwRange = v; loadHwCharts(); });
function renderCores() {
    const h = S.hw; if (!h) return;
    $('cores').innerHTML = (h.cores || []).map((c, i) => `<div class="core"><div style="display:flex;justify-content:space-between"><span class="dim">Core ${i}</span><b>${Math.round(c)} %</b></div>
        <div class="bar"><div style="width:${Math.min(100, c)}%;background-position:${Math.min(100, c)}% 0"></div></div></div>`).join('');
    const st = S.live.host || {};
    $('hwInfo').innerHTML = `<div class="k">Board</div><div class="v">${esc(st.model)}</div><div class="k">System</div><div class="v">${esc(st.os)}</div>
        <div class="k">Kernel</div><div class="v">${esc(st.kernel)} · ${esc(st.arch)}</div><div class="k">Clock</div><div class="v">${h.freq || '–'} MHz of ${st.freq_max || '–'} · ${st.cpus} cores</div>
        <div class="k">Load</div><div class="v">${h.load.join(' · ')}</div><div class="k">Uptime</div><div class="v">${fmtDur(h.uptime)}</div>`;
    $('hwTemps').innerHTML = Object.entries(h.temps || {}).map(([k, v]) => `<div class="k">🌡️ ${esc(k)}</div><div class="v">${v} °C</div>`).join('')
        + `<div class="k">Memory</div><div class="v">${fmtBytes(h.mem_used)} of ${fmtBytes(h.mem_total)}</div>
        <div class="k">Swap</div><div class="v">${fmtBytes(h.swap_used)} of ${fmtBytes(h.swap_total)}</div>
        <div class="k">SD card</div><div class="v">${fmtBytes(h.disk_used)} of ${fmtBytes(h.disk_total)}</div>
        <div class="k">Network ${esc(h.iface)}</div><div class="v">↓ ${fmtRate(h.rx)} · ↑ ${fmtRate(h.tx)}</div>
        <div class="k">Total since boot</div><div class="v">↓ ${fmtBytes(h.rx_total)} · ↑ ${fmtBytes(h.tx_total)}</div>`;
}
async function loadHwCharts() {
    try {
        const d = await api('GET', `/api/stats?range=${S.hwRange}`), hw = d.hw, ts = hw.map(r => r.ts);
        lines($('hwCpu'), ts, [{ name: 'CPU', color: C.cpu, values: hw.map(r => r.cpu), fill: true }, { name: 'Bridge', color: C.bridge, values: hw.map(r => r.bridge_cpu) }], (v, a) => a ? Math.round(v) + ' %' : v.toFixed(1) + ' %', 20);
        lines($('hwTemp'), ts, [{ name: 'Average', color: C.temp, values: hw.map(r => r.temp), fill: true }, { name: 'Max', color: '#ff453a', values: hw.map(r => r.temp_max) }], (v, a) => a ? Math.round(v) + '°' : v.toFixed(1) + ' °C', 60);
        lines($('hwMem'), ts, [{ name: 'Used', color: C.mem, values: hw.map(r => r.mem_used), fill: true }], v => fmtBytes(v), hw.length ? hw[0].mem_total : 0);
        lines($('hwNet'), ts, [{ name: '↓ in', color: C.rx, values: hw.map(r => r.rx), fill: true }, { name: '↑ out', color: C.tx, values: hw.map(r => r.tx) }], v => fmtRate(v));
    } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- admin
const ADMIN = ['services', 'settings', 'backup', 'users', 'audit'];
function showAdmin(a) {
    if (!ADMIN.includes(a)) a = 'services';
    S.admin.tab = a;
    document.querySelectorAll('#adminTabs button').forEach(b => b.classList.toggle('active', b.dataset.a === a));
    ADMIN.forEach(x => $('admin-' + x).classList.toggle('hidden', x !== a));
    ({ services: loadServices, settings: loadSettings, backup: loadBackup, users: loadUsers, audit: loadAudit })[a]()
        .catch(e => $('admin-' + a).innerHTML = `<div class="tile"><div class="empty">⚠️ ${esc(e.message)}</div></div>`);
    if (S.tab === 'admin') history.replaceState(null, '', '#admin/' + a);
}
$('adminTabs').addEventListener('click', e => { const b = e.target.closest('button[data-a]'); if (b) showAdmin(b.dataset.a); });
async function loadServices() {
    const L = await api('GET', '/api/live'), sys = await api('GET', '/api/admin/system');
    S.live.services = L.services; S.live.config = L.config;
    const env = L.config.env || {};
    $('admin-services').innerHTML = `<div class="grid g2">
        <div class="tile"><h3>🌉 Services</h3><div class="subt">Start, stop and restart; the journal shows the last lines.</div>
            ${L.services.map(s => `<div class="svc"><span class="dot ${s.active === 'active' ? 'on' : 'off'}"></span><div style="flex:1;min-width:0"><div class="n">${esc(s.unit)}</div>
                <div class="d">${esc(s.active)} ${s.sub ? '(' + esc(s.sub) + ')' : ''} · PID ${s.pid || '–'} · ${s.restarts} restarts${s.files && s.files.length ? '<br><span class="mono">' + s.files.map(esc).join('<br>') + '</span>' : ''}</div></div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end"><button class="btn small" onclick="svcCtl('${esc(s.unit)}','restart',this)">🔄 Restart</button>
                ${s.active === 'active' ? `<button class="btn small danger" onclick="svcCtl('${esc(s.unit)}','stop',this)">⏹ Stop</button>` : `<button class="btn small good" onclick="svcCtl('${esc(s.unit)}','start',this)">▶ Start</button>`}
                <button class="btn small" onclick="showJournal('${esc(s.unit)}')">📜 Journal</button></div></div>`).join('')}
            <div class="svc"><span class="dot on"></span><div style="flex:1"><div class="n">n8zello</div><div class="d">this dashboard · ${esc(sys.version)} · up ${fmtDur(sys.uptime, true)}</div></div>
                <button class="btn small" onclick="showJournal('n8zello')">📜 Journal</button></div>
            ${S.me.role === 'superadmin' ? '<div class="actions"><button class="btn" onclick="power(\'reboot\')">🔄 Reboot board</button><button class="btn danger" onclick="power(\'poweroff\')">⏻ Shut down</button></div>' : ''}</div>
        <div class="tile"><h3>🧾 Bridge configuration</h3><div class="subt">Environment of ${esc(L.services.find(s => s.bridge)?.unit || 'the bridge')} (systemctl edit); secrets hidden.</div>
            <div class="kv">${Object.entries(env).map(([k, v]) => `<div class="k mono" style="font-size:12px">${esc(k)}</div><div class="v mono" style="font-size:12px">${esc(v)}</div>`).join('') || '<div class="k">–</div><div class="v">no environment found</div>'}</div>
            <div class="kv" style="margin-top:14px"><div class="k">Database</div><div class="v">${fmtBytes(sys.db_size)} · ${num(sys.counts.talks)} talks · ${num(sys.counts.samples)} samples</div>
            <div class="k">Browsers live</div><div class="v">${sys.clients}</div></div></div></div>
        <pre class="term hidden" id="jView" style="height:380px;margin-top:14px"></pre>`;
}
async function svcCtl(unit, action, btn) {
    if (!await confirmBox(`${action[0].toUpperCase() + action.slice(1)} ${unit}?`, action === 'stop' ? 'The bridge is offline until it is started again.' : 'The Zello connection is interrupted for a few seconds.', action === 'stop' ? 'Stop' : 'Do it', action === 'stop' ? 'danger' : 'primary')) return;
    btn.disabled = true;
    try { await api('POST', '/api/admin/service', { unit, action }); toast(`${unit}: ${action}`); loadServices(); } catch (e) { toast(e.message, true); btn.disabled = false; }
}
async function showJournal(unit) { const j = $('jView'); j.classList.remove('hidden'); j.textContent = 'Loading …'; try { j.textContent = (await api('GET', `/api/admin/journal?unit=${encodeURIComponent(unit)}&lines=300`)).text || '(empty)'; j.scrollTop = j.scrollHeight; } catch (e) { j.textContent = e.message; } }
async function power(a) {
    if (!await confirmBox(a === 'reboot' ? 'Reboot the Tinker Board?' : 'Shut down the Tinker Board?', a === 'reboot' ? 'Bridge and dashboard are back after about a minute.' : 'The bridge stays off until the board is powered on again.', a === 'reboot' ? 'Reboot' : 'Shut down', 'danger')) return;
    try { await api('POST', '/api/admin/power', { action: a }); toast(a === 'reboot' ? 'Rebooting …' : 'Shutting down …'); } catch (e) { toast(e.message, true); }
}
function fieldHtml(f) {
    const lbl = `<label for="f_${f.key}">${esc(f.label)}</label>`, help = f.help ? `<div class="help">${esc(f.help)}</div>` : '';
    let input;
    if (f.kind === 'bool') input = `<label class="switch"><input type="checkbox" id="f_${f.key}" name="${f.key}" ${f.value ? 'checked' : ''}><span></span></label>`;
    else if (f.kind === 'int') input = `<input type="number" id="f_${f.key}" name="${f.key}" value="${esc(f.value)}" ${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''}>`;
    else input = `<input type="text" id="f_${f.key}" name="${f.key}" value="${esc(f.value)}">`;
    return `<div class="row">${lbl}${input}${help}</div>`;
}
async function loadSettings() {
    const d = await api('GET', '/api/admin/settings');
    $('admin-settings').innerHTML = `<div class="tiles">${d.sections.map(s => `<form class="tile" onsubmit="event.preventDefault();saveSettings(this)"><h3>${esc(s.title)}</h3><div class="subt">${esc(s.subtitle)}</div>
        ${s.fields.map(fieldHtml).join('')}<div class="actions"><button class="btn primary">Save</button></div></form>`).join('')}</div>`;
}
async function saveSettings(f) {
    const v = {}; f.querySelectorAll('[name]').forEach(i => v[i.name] = i.type === 'checkbox' ? i.checked : i.value);
    try { const r = await api('POST', '/api/admin/settings', { changes: v }); toast(r.changed.length ? `Saved (${r.changed.length})` : 'No changes'); S.info = await api('GET', '/api/info'); renderHeader(); } catch (e) { toast(e.message, true); }
}
async function loadBackup() {
    $('admin-backup').innerHTML = `<div class="grid g2"><div class="tile"><h3>💾 Backup</h3>
        <div class="subt">One .tar.gz with everything needed to set the bridge up again:</div>
        <ul class="dim" style="margin:6px 0 10px 18px;line-height:1.7"><li>systemd unit and drop-ins of asl-zello-bridge (Zello account, channel, USRP ports)</li>
        <li>Zello key file (if configured)</li><li>SvxLink configuration (/etc/svxlink) and /root/svxlink-usrp.sh, if present</li><li>the dashboard database (statistics, admins)</li></ul>
        <div class="notice-box dim" style="font-size:12.5px">🔒 Contains passwords and keys – keep the file safe, do not upload it anywhere public.</div>
        <div class="actions"><a class="btn primary" href="/api/admin/backup" download>⬇️ Download backup</a></div></div>
        <div class="tile"><h3>♻️ Restore</h3><div class="subt">On a fresh DietPi with asl-zello-bridge installed:</div>
        <pre class="term" style="height:auto">sudo tar xzf zellobridge-backup-*.tar.gz -C /
sudo systemctl daemon-reload
sudo systemctl restart asl-zello-bridge</pre><div class="subt" style="margin-top:8px">Details: docs/de/betrieb.md in the repository.</div></div></div>`;
    return Promise.resolve();
}
async function loadUsers() {
    const d = await api('GET', '/api/admin/users'), sa = S.me.role === 'superadmin';
    $('admin-users').innerHTML = `<div class="grid users-grid"><div class="tile"><h3>👥 Admins</h3><div class="subt">The superadmin is set by the installation and manages the admins.</div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>Role</th><th class="hide-sm">Last login</th><th></th></tr></thead><tbody>
        ${d.users.map(u => `<tr><td><b>${esc(u.username)}</b><br><span class="dim">${esc(u.display_name)}</span></td><td><span class="pill ${u.role === 'superadmin' ? 'warn' : 'ok'}">${u.role}</span></td>
            <td class="hide-sm">${fmtDate(u.last_login)}</td><td class="num">${sa && u.role !== 'superadmin' ? `<button class="btn small danger" onclick="delUser(${u.id},'${esc(u.username)}')">Delete</button>` : ''}</td></tr>`).join('')}</tbody></table></div></div>
        ${sa ? `<form class="tile" onsubmit="event.preventDefault();addUser(this)"><h3>➕ New admin</h3>
            <div class="row"><label>Username</label><input type="text" name="username" required></div><div class="row"><label>Name</label><input type="text" name="display_name"></div>
            <div class="row"><label>Password (min. 10)</label><input type="password" name="password" required minlength="10" autocomplete="new-password"></div>
            <div class="actions"><button class="btn primary">Create</button></div></form>` : ''}</div>`;
}
async function addUser(f) { try { await api('POST', '/api/admin/users', { username: f.username.value, display_name: f.display_name.value, password: f.password.value }); toast('Admin created'); loadUsers(); } catch (e) { toast(e.message, true); } }
async function delUser(id, n) { if (!await confirmBox(`Delete ${n}?`, 'The admin and all sessions are removed.', 'Delete', 'danger')) return; try { await api('DELETE', `/api/admin/users/${id}`, {}); loadUsers(); } catch (e) { toast(e.message, true); } }
async function loadAudit() {
    const d = await api('GET', '/api/admin/audit');
    const lbl = { login: '🔓 Login', login_failed: '⛔ Login failed', settings_changed: '⚙️ Settings', service: '🌉 Service', backup: '💾 Backup', password_changed: '🔑 Password',
                  user_created: '➕ Admin', user_deleted: '🗑️ Admin', superadmin_set: '👑 Superadmin', password_reset: '🔑 Reset', reboot: '🔄 Reboot', poweroff: '⏻ Shutdown' };
    $('admin-audit').innerHTML = `<div class="grid g2"><div class="tile"><h3>🔑 Active sessions</h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>User</th><th>IP</th><th>Since</th></tr></thead><tbody>
        ${d.sessions.map(s => `<tr><td>${esc(s.username)}</td><td class="mono">${esc(s.ip)}</td><td>${fmtDate(s.created_at)}</td></tr>`).join('')}</tbody></table></div></div>
        <div class="tile"><h3>📜 Change log</h3><div class="tbl-wrap" style="max-height:520px;overflow:auto"><table class="tbl"><thead><tr><th>Time</th><th>What</th><th>Who</th><th class="hide-sm">Details</th></tr></thead><tbody>
        ${d.entries.map(e => `<tr><td>${fmtDate(e.ts)}</td><td>${lbl[e.action] || esc(e.action)}</td><td>${esc(e.username)}</td><td class="hide-sm dim" style="white-space:normal">${esc(e.detail)}${e.ip ? ' · ' + esc(e.ip) : ''}</td></tr>`).join('')}</tbody></table></div></div></div>`;
}

// collapsible section headers (state kept per browser, as on the DMR dashboard)
(function () {
    const KEY = 'n8zello.collapsed'; let st = {}; try { st = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) {}
    const apply = k => { document.querySelectorAll(`[data-section="${k}"]`).forEach(h => h.classList.toggle('collapsed', !!st[k])); document.querySelectorAll(`[data-body="${k}"]`).forEach(b => b.classList.toggle('hidden', !!st[k])); };
    document.querySelectorAll('.collapsible[data-section]').forEach(h => { apply(h.dataset.section); h.addEventListener('click', () => { st[h.dataset.section] = !st[h.dataset.section]; try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} apply(h.dataset.section); }); });
})();
setInterval(() => { if (S.live && S.tab === 'live') { renderToday(); } }, 30000);
boot();
