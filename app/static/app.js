/* CubixOne portal: one page, hash routes (#/home, #/item/3 ...), talking to /api. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { state: null, user: null };
const ROOT_NAME = { reports: 'Reports', dashboards: 'Dashboards' };
const isAdmin = () => S.user && S.user.role === 'admin';
const canBuild = () => S.user && ['admin', 'developer'].includes(S.user.role);

/* ---------------------------------------------------------------- api */
async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: { 'X-CubixOne': '1' }, credentials: 'same-origin' };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (r.status === 401 && !path.startsWith('/api/login') && !path.startsWith('/api/state')) {
    S.user = null; location.hash = '#/login';
  }
  if (!r.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg || JSON.stringify(d)).join('; ');
    throw new Error(msg || `Request failed (${r.status})`);
  }
  return data;
}

/* ---------------------------------------------------------------- small helpers */
let toastTimer;
function toast(msg) {
  let t = $('.toast');
  if (!t) { t = document.createElement('div'); t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.style.display = 'none'; }, 3200);
}
function modal(html) {
  closeModal();
  const v = document.createElement('div'); v.className = 'veil';
  v.innerHTML = `<div class="modal">${html}</div>`;
  v.addEventListener('mousedown', (e) => { if (e.target === v) closeModal(); });
  document.body.appendChild(v);
  const first = v.querySelector('input,select,textarea'); if (first) first.focus();
}
function closeModal() { document.querySelectorAll('.veil').forEach((v) => v.remove()); }
const pill = (t, c) => `<span class="pill ${c}">${esc(t)}</span>`;
const visPill = (v) => v === 'restricted' ? pill('Restricted', 'p-orange') : pill('Everyone', 'p-teal');
const kindPill = (k) => ({ upload: pill('Uploaded HTML', 'p-blue'), live: pill('Live app', 'p-cream'), generated: pill('Built with AI', 'p-blue') }[k] || '');
const statusPill = (s) => s === 'ok' ? pill('Connected', 'p-teal') : s === 'bad' ? pill('Failed', 'p-red') : pill('Not tested', 'p-grey');
const when = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';
const val = (id) => { const e = document.getElementById(id); return e ? e.value : ''; };
function wordmark() {
  const b = (S.state && S.state.brand) || 'CubixOne';
  return b === 'CubixOne' ? '<span class="metal">Cubix</span><span class="one">One</span>' : `<span class="metal">${esc(b)}</span>`;
}
async function busy(btn, fn) {
  const old = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${old}`; }
  try { return await fn(); } catch (e) { toast(e.message); return undefined; } finally { if (btn) { btn.disabled = false; btn.innerHTML = old; } }
}

/* ---------------------------------------------------------------- router */
let renderNo = 0;
let afterRender = null;
window.addEventListener('hashchange', route);

async function loadState() {
  S.state = await api('/api/state');
  S.user = S.state.user;
  document.title = S.state.brand || 'CubixOne';
}

async function route() {
  const no = ++renderNo;
  if (!S.state) {
    try { await loadState(); } catch (e) { $('#app').innerHTML = `<div class="center"><div class="panel"><h2>CubixOne cannot start</h2><p class="muted">${esc(e.message)}</p></div></div>`; return; }
  }
  const h = location.hash.replace(/^#\/?/, '') || 'home';
  const [page, a, b] = h.split('/');
  if (S.state.setup) return renderSetup();
  if (!S.user) { if (page !== 'login') { location.hash = '#/login'; return; } return renderLogin(); }
  if (S.user.must_change && page !== 'password') { location.hash = '#/password'; return; }
  if (page === 'login') { location.hash = '#/home'; return; }
  if (page === 'password') return renderPassword(true);

  const pages = {
    home: pageHome, section: pageSection, folder: pageFolder, item: pageItem, assistant: pageAssistant,
    developer: pageDeveloper, account: () => passwordCard(false), search: pageSearch,
    admin: (x, y) => (({ connections: pageConnections, llm: pageLLM, users: pageUsers, audit: pageAudit, settings: pageSettings })[x] || pageConnections)(y),
  };
  const active = page === 'admin' ? `admin/${a || 'connections'}` : page === 'section' ? `section/${a}` : page;
  shell(active);
  try {
    const html = await (pages[page] || pageHome)(a, b);
    if (no !== renderNo) return;
    $('#page').innerHTML = html;
    if (afterRender) { const f = afterRender; afterRender = null; f(); }
  } catch (e) {
    if (no !== renderNo) return;
    $('#page').innerHTML = `<div class="card err" style="max-width:560px"><h2>Something went wrong</h2><p class="muted">${esc(e.message)}</p><a class="btn" href="#/home">Back to Home</a></div>`;
  }
}

function shell(active) {
  const link = (h, ic, label) => `<a href="#/${h}" class="${active === h ? 'on' : ''}"><span class="ic">${ic}</span>${label}</a>`;
  const initials = (S.user.full_name || S.user.username).split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  $('#app').innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand">${wordmark()}</div>
      <nav class="nav">
        ${link('home', '⌂', 'Home')}
        ${link('section/reports', '▤', 'Reports')}
        ${link('section/dashboards', '◧', 'Dashboards')}
        ${S.state.bot ? link('assistant', '✦', 'Assistant') : ''}
        ${canBuild() ? `<div class="sec">Build</div>${link('developer', '⌘', 'Developer mode')}` : ''}
        ${isAdmin() ? `<div class="sec">Administration</div>
          ${link('admin/connections', '⛁', 'Data connections')}
          ${link('admin/llm', '✧', 'LLM &amp; Bot')}
          ${link('admin/users', '☺', 'Users &amp; access')}
          ${link('admin/audit', '☰', 'Audit log')}
          ${link('admin/settings', '⚙', 'Settings')}` : ''}
      </nav>
      <div class="foot">CubixOne v${esc(S.state.version)} · by Cubix Labs</div>
    </aside>
    <div class="main">
      <header class="top">
        <form class="grow" onsubmit="event.preventDefault(); location.hash='#/search/'+encodeURIComponent(this.q.value.trim())" style="max-width:460px">
          <input class="search" name="q" placeholder="Search reports and dashboards…" style="width:100%"></form>
        <div class="grow"></div>
        <a href="#/account" class="small muted" title="Change password">${esc(S.user.full_name || S.user.username)} · ${esc(S.user.role)}</a>
        <div class="avatar">${esc(initials)}</div>
        <button class="btn sm" onclick="signOut()">Sign out</button>
      </header>
      <main class="page" id="page"><div class="muted"><span class="spin"></span>Loading…</div></main>
    </div>
  </div>`;
}

async function signOut() {
  try { await api('/api/logout', { method: 'POST' }); } catch (_) { /* already out */ }
  S.user = null; S.state = null; location.hash = '#/login'; route();
}

/* ---------------------------------------------------------------- first run, sign-in, password */
function renderSetup() {
  $('#app').innerHTML = `
  <div class="center"><div class="panel">
    <div class="row" style="justify-content:space-between"><div class="wordmark">${wordmark()}</div><div class="small muted">First-time setup</div></div>
    <div class="steps"><div class="on"></div><div></div></div>
    <h2>Create the first administrator</h2>
    <p class="muted">The database for accounts was chosen when CubixOne was installed. Now name the portal and create the account that runs it.</p>
    <form onsubmit="event.preventDefault(); doSetup(this)">
      <div class="grid g2" style="gap:0 14px">
        <div class="field"><label>Portal name</label><input id="su_brand" value="CubixOne"></div>
        <div class="field"><label>Full name</label><input id="su_name"></div>
        <div class="field"><label>Username</label><input id="su_user" autocomplete="username" required></div>
        <div class="field"><label>Password</label><input id="su_pass" type="password" autocomplete="new-password" required></div>
      </div>
      <p class="small muted">At least ${S.state.password_min_length} characters, mixing three of: lower case, capitals, digits, symbols.</p>
      <div class="row" style="justify-content:flex-end"><button class="btn primary">Create and sign in</button></div>
    </form>
  </div></div>`;
}
async function doSetup(form) {
  await busy(form.querySelector('button'), async () => {
    const r = await api('/api/setup', { method: 'POST', body: { brand: val('su_brand'), full_name: val('su_name'), username: val('su_user'), password: val('su_pass') } });
    S.state = null; S.user = r.user; location.hash = '#/home'; route();
  });
}

function renderLogin() {
  $('#app').innerHTML = `
  <div class="center"><div class="panel" style="width:min(420px,100%)">
    <div class="wordmark" style="margin-bottom:6px">${wordmark()}</div>
    <div class="small muted" style="margin-bottom:20px">Reports, dashboards and AI, end to end</div>
    <form onsubmit="event.preventDefault(); doLogin(this)">
      <div class="field"><label>Username</label><input id="li_user" autocomplete="username" required></div>
      <div class="field"><label>Password</label><input id="li_pass" type="password" autocomplete="current-password" required></div>
      <div id="li_err"></div>
      <button class="btn primary" style="width:100%;justify-content:center">Sign in</button>
    </form>
    <p class="small muted" style="margin-top:14px">Accounts are created by an administrator.</p>
  </div></div>`;
}
async function doLogin(form) {
  const btn = form.querySelector('button');
  btn.disabled = true;
  try {
    const r = await api('/api/login', { method: 'POST', body: { username: val('li_user'), password: val('li_pass') } });
    S.user = r.user; S.state = null; location.hash = r.user.must_change ? '#/password' : '#/home'; route();
  } catch (e) {
    $('#li_err').innerHTML = `<div class="test bad" style="margin-bottom:12px">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
}

function passwordCard(forced) {
  return `<div class="card" style="max-width:480px">
    <h2>${forced ? 'Choose a new password' : 'Change your password'}</h2>
    ${forced ? '<p class="muted">Your password was set by an administrator. Choose your own before you continue.</p>' : ''}
    <form onsubmit="event.preventDefault(); doPassword(this, ${forced})">
      <div class="field"><label>Current password</label><input id="pw_cur" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>New password</label><input id="pw_new" type="password" autocomplete="new-password" required></div>
      <div class="field"><label>Repeat new password</label><input id="pw_rep" type="password" autocomplete="new-password" required></div>
      <p class="small muted">At least ${S.state.password_min_length} characters, mixing three of: lower case, capitals, digits, symbols; not containing your username.</p>
      <button class="btn primary">Save password</button>
    </form></div>`;
}
function renderPassword(forced) {
  $('#app').innerHTML = `<div class="center">${passwordCard(forced)}</div>`;
}
async function doPassword(form, forced) {
  if (val('pw_new') !== val('pw_rep')) { toast('The new passwords do not match.'); return; }
  await busy(form.querySelector('button'), async () => {
    const r = await api('/api/account/password', { method: 'POST', body: { current: val('pw_cur'), new: val('pw_new') } });
    S.user = r.user; toast('Password changed');
    if (forced) { S.state = null; location.hash = '#/home'; route(); } else form.reset();
  });
}

/* ---------------------------------------------------------------- catalogue */
function itemTable(items, opts = {}) {
  if (!items.length) return `<div class="card empty">${opts.empty || 'Nothing here yet.'}</div>`;
  return `<div class="card" style="padding:6px 8px"><table>
    <tr><th>Name</th><th>Type</th><th>Access</th>${opts.folder ? '<th>Folder</th>' : ''}<th>Data source</th><th>Updated</th>${isAdmin() ? '<th></th>' : ''}</tr>
    ${items.map((i) => `<tr class="click" onclick="location.hash='#/item/${i.id}'">
      <td><div class="row" style="gap:10px"><span class="item-ic" style="background:${i.root === 'reports' ? '#10203F' : '#16233F'}">${i.root === 'reports' ? '▤' : '◧'}</span><b>${esc(i.title)}</b></div></td>
      <td>${kindPill(i.kind)}</td><td>${visPill(i.visibility)}</td>
      ${opts.folder ? `<td class="small muted">${esc(ROOT_NAME[i.root])}${i.folder_path ? ' / ' + esc(i.folder_path) : ''}</td>` : ''}
      <td class="small muted">${esc(i.source || '—')}</td><td class="small muted">${when(i.updated_at)}</td>
      ${isAdmin() ? `<td class="right"><button class="btn sm" onclick="event.stopPropagation(); manageItem(${i.id})">Manage</button></td>` : ''}
    </tr>`).join('')}</table></div>`;
}

async function pageHome() {
  const d = await api('/api/home');
  return `
  <div class="hero"><div><h1>Welcome, ${esc(S.user.full_name || S.user.username)}</h1><div class="muted">Everything shared with you, in two places.</div></div>
    ${S.state.bot ? `<a class="btn accent" href="#/assistant">✦ Ask the Assistant</a>` : ''}</div>
  <div class="grid g2" style="margin-bottom:22px">
    <div class="tile rep" onclick="location.hash='#/section/reports'"><h2>Reports</h2><div>Tables and printable reports</div><div class="count">${d.counts.reports}</div></div>
    <div class="tile dash" onclick="location.hash='#/section/dashboards'"><h2>Dashboards</h2><div>KPIs, charts and live apps</div><div class="count">${d.counts.dashboards}</div></div>
  </div>
  <h3>Recently updated</h3>${itemTable(d.recent, { folder: true, empty: 'No reports or dashboards have been shared with you yet.' })}`;
}

async function pageSearch(q) {
  q = decodeURIComponent(q || '');
  const d = await api('/api/catalog?q=' + encodeURIComponent(q));
  return `<div class="hero"><div><h1>Search</h1><div class="muted">${d.items.length} result(s) for “${esc(q)}”</div></div></div>${itemTable(d.items, { folder: true })}`;
}

const pageSection = (root) => folderView(`/api/catalog?root=${encodeURIComponent(root || 'reports')}`);
const pageFolder = (id) => folderView(`/api/catalog?folder_id=${Number(id)}`);

let CAT = null;   // the catalogue view on screen, for the dialogs
async function folderView(url) {
  const d = CAT = await api(url);
  const f = d.folder, root = d.root, sing = root === 'reports' ? 'report' : 'dashboard';
  return `
  <div class="crumbs"><a href="#/home">Home</a> › <a href="#/section/${root}">${ROOT_NAME[root]}</a>${d.path.map((p) => ` › <a href="#/folder/${p.id}">${esc(p.name)}</a>`).join('')}</div>
  <div class="hero">
    <div><h1>${f ? esc(f.name) : ROOT_NAME[root]}</h1><div class="muted">${f ? visPill(f.visibility) : `Every ${sing} you can open`}</div></div>
    <div class="row">
      ${isAdmin() ? `<button class="btn" onclick="newFolder()">＋ Subfolder</button>` : ''}
      ${isAdmin() && f ? `<button class="btn" onclick="manageFolder()">Folder settings</button>` : ''}
      ${isAdmin() ? `<button class="btn" onclick="registerLive()">⇄ Register live ${sing}</button>` : ''}
      ${canBuild() ? `<button class="btn primary" onclick="uploadItem()">⇪ Upload ${sing}</button>` : ''}
    </div>
  </div>
  ${d.folders.length ? `<h3>Folders</h3><div class="grid g4" style="margin-bottom:22px">${d.folders.map((s) => `
    <div class="card folder" onclick="location.hash='#/folder/${s.id}'"><div class="fi">▣</div><div><b>${esc(s.name)}</b><div class="small muted">${s.count} item(s) · ${s.visibility === 'restricted' ? 'Restricted' : 'Everyone'}</div></div></div>`).join('')}</div>` : ''}
  <h3>${f ? 'Items' : 'All ' + ROOT_NAME[root].toLowerCase()}</h3>
  ${itemTable(d.items, { folder: !f })}`;
}

function folderOptions(selected) {
  return `<option value="">${ROOT_NAME[CAT.root]} (top level)</option>` +
    CAT.all_folders.sort((a, b) => a.path.localeCompare(b.path)).map((x) => `<option value="${x.id}" ${x.id === selected ? 'selected' : ''}>${esc(x.path)}</option>`).join('');
}

function newFolder() {
  modal(`<h2>New subfolder</h2>
    <div class="field"><label>Name</label><input id="nf_name"></div>
    <div class="field"><label>Who can open it</label><select id="nf_vis"><option value="everyone">Everyone signed in</option><option value="restricted">Restricted: only people granted this folder</option></select></div>
    <div class="row" style="justify-content:flex-end"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="doNewFolder(this)">Create</button></div>`);
}
async function doNewFolder(btn) {
  await busy(btn, async () => {
    await api('/api/folders', { method: 'POST', body: { root: CAT.root, parent_id: CAT.folder ? CAT.folder.id : null, name: val('nf_name'), visibility: val('nf_vis') } });
    closeModal(); toast('Folder created'); route();
  });
}

async function userChecklist(selectedIds) {
  const d = await api('/api/users');
  const sel = new Set(selectedIds);
  return `<div class="checklist">${d.users.filter((u) => u.role !== 'admin').map((u) => `
    <label><input type="checkbox" class="grant" value="${u.id}" ${sel.has(u.id) ? 'checked' : ''}> ${esc(u.full_name || u.username)} <span class="muted">(${esc(u.username)} · ${esc(u.role)})</span></label>`).join('') || '<div class="muted small">No non-admin users yet.</div>'}</div>
    <p class="small muted">Administrators always have access.</p>`;
}
const checkedGrants = () => [...document.querySelectorAll('.grant:checked')].map((c) => Number(c.value));

async function manageFolder() {
  const f = CAT.folder;
  const g = await api(`/api/folders/${f.id}/grants`);
  modal(`<h2>Folder: ${esc(f.name)}</h2>
    <div class="field"><label>Name</label><input id="mf_name" value="${esc(f.name)}"></div>
    <div class="field"><label>Who can open it</label><select id="mf_vis"><option value="everyone" ${f.visibility === 'everyone' ? 'selected' : ''}>Everyone signed in</option><option value="restricted" ${f.visibility === 'restricted' ? 'selected' : ''}>Restricted: only the people ticked below</option></select></div>
    <p class="small muted">Restricting a folder closes everything beneath it, at every level.</p>
    <div class="field"><label>People granted this folder</label></div>${await userChecklist(g.user_ids)}
    <div class="row" style="justify-content:space-between;margin-top:14px">
      <button class="btn" style="color:var(--bad)" onclick="deleteFolder(this)">Remove folder</button>
      <div class="row"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveFolder(this)">Save</button></div></div>`);
}
async function saveFolder(btn) {
  await busy(btn, async () => {
    await api(`/api/folders/${CAT.folder.id}`, { method: 'PATCH', body: { name: val('mf_name'), visibility: val('mf_vis') } });
    await api(`/api/folders/${CAT.folder.id}/grants`, { method: 'PUT', body: { user_ids: checkedGrants() } });
    closeModal(); toast('Folder saved'); route();
  });
}
async function deleteFolder(btn) {
  if (!confirm('Remove this folder? Only an empty folder can be removed.')) return;
  await busy(btn, async () => {
    await api(`/api/folders/${CAT.folder.id}`, { method: 'DELETE' });
    closeModal(); toast('Folder removed');
    location.hash = CAT.path.length > 1 ? `#/folder/${CAT.path[CAT.path.length - 2].id}` : `#/section/${CAT.root}`;
  });
}

function uploadItem() {
  const sing = CAT.root === 'reports' ? 'report' : 'dashboard';
  modal(`<h2>Upload a ${sing}</h2>
    <div class="drop"><input type="file" id="up_file" accept=".html,.htm,.zip"><div class="small" style="margin-top:8px">An <b>.html</b> file, or a <b>.zip</b> package of the page with its images, CSS and JS.</div></div>
    <div class="grid g2" style="gap:0 14px;margin-top:14px">
      <div class="field"><label>Title</label><input id="up_title"></div>
      <div class="field"><label>Folder</label><select id="up_folder">${folderOptions(CAT.folder ? CAT.folder.id : null)}</select></div>
      <div class="field"><label>Who can open it</label><select id="up_vis"><option value="everyone">Everyone signed in</option><option value="restricted">Restricted: named people</option></select></div>
    </div>
    <p class="small muted">The page runs in a sandbox: its scripts cannot read the portal's cookies or pages. The Assistant reads its text.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="doUpload(this)">Upload</button></div>`);
}
async function doUpload(btn) {
  const file = $('#up_file').files[0];
  if (!file) { toast('Choose a file first'); return; }
  const fd = new FormData();
  fd.append('root', CAT.root); fd.append('title', val('up_title') || file.name.replace(/\.(html?|zip)$/i, ''));
  fd.append('visibility', val('up_vis')); fd.append('folder_id', val('up_folder')); fd.append('file', file);
  await busy(btn, async () => {
    const r = await api('/api/items/upload', { method: 'POST', body: fd });
    closeModal(); toast('Uploaded'); location.hash = `#/item/${r.id}`;
  });
}

function registerLive() {
  const sing = CAT.root === 'reports' ? 'report' : 'dashboard';
  modal(`<h2>Register a live ${sing}</h2>
    <p class="muted small">For a ${sing} that runs as its own app (Streamlit, Dash, Superset, a Node or Python service). The portal frames it; the app can ask <code>/api/auth/check?item=&lt;id&gt;</code> whether the viewer may open it.</p>
    <div class="field"><label>Title</label><input id="lv_title"></div>
    <div class="field"><label>Address</label><input id="lv_url" placeholder="https://reports.example.com/sales/"></div>
    <div class="grid g2" style="gap:0 14px">
      <div class="field"><label>Folder</label><select id="lv_folder">${folderOptions(CAT.folder ? CAT.folder.id : null)}</select></div>
      <div class="field"><label>Who can open it</label><select id="lv_vis"><option value="everyone">Everyone signed in</option><option value="restricted">Restricted: named people</option></select></div>
    </div>
    <div class="row" style="justify-content:flex-end"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="doRegister(this)">Register</button></div>`);
}
async function doRegister(btn) {
  await busy(btn, async () => {
    const r = await api('/api/items/live', { method: 'POST', body: { root: CAT.root, title: val('lv_title'), url: val('lv_url'), folder_id: val('lv_folder') ? Number(val('lv_folder')) : null, visibility: val('lv_vis') } });
    closeModal(); toast('Registered'); location.hash = `#/item/${r.id}`;
  });
}

async function manageItem(id) {
  const it = await api(`/api/items/${id}`);
  if (!CAT || CAT.root !== it.root) CAT = await api(`/api/catalog?root=${it.root}`);
  const g = await api(`/api/items/${id}/grants`);
  modal(`<h2>Manage: ${esc(it.title)}</h2>
    <div class="grid g2" style="gap:0 14px">
      <div class="field"><label>Title</label><input id="mi_title" value="${esc(it.title)}"></div>
      <div class="field"><label>Folder (within ${ROOT_NAME[it.root]})</label><select id="mi_folder">${folderOptions(it.folder_id)}</select></div>
      <div class="field"><label>Who can open it</label><select id="mi_vis"><option value="everyone" ${it.visibility === 'everyone' ? 'selected' : ''}>Everyone signed in</option><option value="restricted" ${it.visibility === 'restricted' ? 'selected' : ''}>Restricted: the people ticked below</option></select></div>
    </div>
    <div class="field"><label>People granted this item</label></div>${await userChecklist(g.user_ids)}
    <div class="row" style="justify-content:space-between;margin-top:14px">
      <button class="btn" style="color:var(--bad)" onclick="deleteItem(${id}, this)">Delete</button>
      <div class="row"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveItem(${id}, this)">Save</button></div></div>`);
}
async function saveItem(id, btn) {
  await busy(btn, async () => {
    await api(`/api/items/${id}`, { method: 'PATCH', body: { title: val('mi_title'), visibility: val('mi_vis'), move: true, folder_id: val('mi_folder') ? Number(val('mi_folder')) : null } });
    await api(`/api/items/${id}/grants`, { method: 'PUT', body: { user_ids: checkedGrants() } });
    closeModal(); toast('Saved'); route();
  });
}
async function deleteItem(id, btn) {
  if (!confirm('Delete this item and its files? This cannot be undone.')) return;
  await busy(btn, async () => {
    await api(`/api/items/${id}`, { method: 'DELETE' });
    closeModal(); toast('Deleted');
    if (location.hash.startsWith('#/item/')) location.hash = '#/home'; else route();
  });
}

/* ---------------------------------------------------------------- viewer + Assistant */
async function pageItem(id) {
  const it = await api(`/api/items/${Number(id)}`);
  const sandbox = it.kind === 'live' ? '' : 'sandbox="allow-scripts allow-forms allow-popups allow-modals allow-downloads"';
  return `
  <div class="crumbs"><a href="#/home">Home</a> › <a href="#/section/${it.root}">${ROOT_NAME[it.root]}</a>${it.folder_path ? ' › ' + esc(it.folder_path) : ''}</div>
  <div class="hero"><div><h1>${esc(it.title)}</h1><div class="row" style="gap:6px">${kindPill(it.kind)} ${visPill(it.visibility)} <span class="small muted">· ${esc(it.source || '')}</span></div></div>
    <div class="row"><a class="btn" href="${esc(it.view_url)}" target="_blank" rel="noopener">↗ Full screen</a>${isAdmin() ? `<button class="btn" onclick="manageItem(${it.id})">Manage</button>` : ''}</div></div>
  <div class="viewer" style="${S.state.bot && it.kind !== 'live' ? '' : 'grid-template-columns:1fr'}">
    <iframe class="frame" ${sandbox} src="${esc(it.view_url)}" title="${esc(it.title)}" referrerpolicy="same-origin"></iframe>
    ${S.state.bot && it.kind !== 'live' ? botPanel(it) : ''}
  </div>`;
}

const CHAT = { key: null, msgs: [] };
function botPanel(it) {
  const key = it ? it.id : 'all';
  if (CHAT.key !== key) { CHAT.key = key; CHAT.msgs = []; }
  afterRender = () => { const m = $('#msgs'); if (m) m.scrollTop = 1e6; };
  return `<div class="card bot">
    <div class="bot-head"><b>✦ Assistant</b><div class="small muted">${it ? `Answers from “${esc(it.title)}” and other items you can open` : 'Answers from the reports and dashboards you can open'} · ${esc(S.state.model)}</div></div>
    <div class="msgs" id="msgs">${CHAT.msgs.length ? CHAT.msgs.map(chatHtml).join('') : `<div class="msg b">Ask me anything about ${it ? 'this ' + (it.root === 'reports' ? 'report' : 'dashboard') : 'your reports and dashboards'}.</div>`}</div>
    <form class="compose" onsubmit="event.preventDefault(); ask(this, ${it ? it.id : 'null'})">
      <input name="q" placeholder="Ask a question…" autocomplete="off"><button class="btn accent">Send</button></form>
  </div>`;
}
const chatHtml = (m) => `<div class="msg ${m.who}">${esc(m.text)}${m.src && m.src.length ? `<span class="src">Source: ${m.src.map(esc).join(', ')}</span>` : ''}</div>`;
async function ask(form, itemId) {
  const q = form.q.value.trim(); if (!q) return;
  form.q.value = '';
  const history = CHAT.msgs.filter((m) => m.who !== 'e').map((m) => ({ role: m.who === 'u' ? 'user' : 'assistant', content: m.text }));
  CHAT.msgs.push({ who: 'u', text: q });
  const box = $('#msgs');
  box.innerHTML = CHAT.msgs.map(chatHtml).join('') + '<div class="msg b muted"><span class="spin"></span>Thinking…</div>'; box.scrollTop = 1e6;
  try {
    const r = await api('/api/assistant', { method: 'POST', body: { question: q, item_id: itemId, history } });
    CHAT.msgs.push({ who: 'b', text: r.answer, src: r.sources });
  } catch (e) { CHAT.msgs.push({ who: 'e', text: '⚠ ' + e.message }); }
  box.innerHTML = CHAT.msgs.map((m) => chatHtml(m.who === 'e' ? { ...m, who: 'b' } : m)).join(''); box.scrollTop = 1e6;
}
async function pageAssistant() {
  if (!S.state.bot) return `<div class="card" style="max-width:560px"><h2>The Assistant is not set up</h2><p class="muted">An administrator connects an AI model under LLM &amp; Bot.</p></div>`;
  return `<div class="hero"><div><h1>Assistant</h1><div class="muted">Ask across every report and dashboard you are allowed to open. Items you cannot open stay hidden from the Assistant too.</div></div></div>
  <div style="max-width:820px">${botPanel(null)}</div>`;
}

/* ---------------------------------------------------------------- Developer mode */
const D = { conns: null, open: {}, schema: {}, sel: null, cols: null, picked: [], prompts: [], stage: 'idle', kind: null, draft: null, msgs: [], docs: [], text: '' };
const SKILL_RE = /\buse\s+(?:the\s+)?([\w.-]+)\s+skills?\b(?:\s+(?:from|at|in)\s+("[^"]+"|'[^']+'|\S+))?\s*[,.;]?\s*(?:and\s+)?/gi;
const stripSkills = (t) => t.replace(SKILL_RE, '').replace(/[\s,;]+(?:and|then|also)?\s*$/i, '').trim();
const isPicked = (o) => D.picked.some((p) => p.conn_id === o.conn_id && p.kind === o.kind && p.name === o.name);
const connById = (id) => (D.conns || []).find((c) => c.id === id);

async function pageDeveloper() {
  if (!D.conns) D.conns = (await api('/api/connections')).connections;
  const busyNow = D.stage === 'askKind' || D.stage === 'building' || D.stage === 'askFolder';
  const tree = D.conns.map((c) => {
    const open = D.open[c.id], sch = D.schema[c.id];
    const n = D.picked.filter((p) => p.conn_id === c.id).length;
    let body = '';
    if (open) {
      if (!sch) body = '<div class="obj muted small"><span class="spin"></span>Reading the schema…</div>';
      else if (sch.error) body = `<div class="obj small" style="color:var(--bad)">${esc(sch.error)}</div>`;
      else body = ['tables', 'views', 'procs'].map((k) => {
        const names = sch[k] || [];
        const gk = `${c.id}:${k}`, gopen = D.open[gk] !== false;
        return `<div class="grp" data-g="${esc(gk)}" onclick="toggleGroup(this.dataset.g)">${gopen ? '▾' : '▸'} ${{ tables: '▦ Tables', views: '◫ Views', procs: 'ƒ Procedures' }[k]} <span class="muted small" style="font-weight:400">(${names.length})</span></div>` +
          (gopen ? names.map((nm) => { const o = { conn_id: c.id, kind: k, name: nm }; const on = D.sel && D.sel.conn_id === c.id && D.sel.name === nm && D.sel.kind === k;
            return `<div class="obj ${on ? 'on' : ''}" data-c="${c.id}" data-k="${k}" data-n="${esc(nm)}">
              <input type="checkbox" ${isPicked(o) ? 'checked' : ''} ${busyNow ? 'disabled' : ''} onclick="pickObj(this.parentNode)">
              <span class="n" title="${esc(nm)}" onclick="selectObj(this.parentNode)">${esc(nm)}</span></div>`; }).join('') : '');
      }).join('');
    }
    return `<div class="conn" onclick="toggleConn(${c.id})"><span class="small">${open ? '▾' : '▸'}</span>
        <span class="dbic" style="background:#1B4DDB;width:22px;height:22px;font-size:9px">${esc((c.label || '').slice(0, 2).toUpperCase())}</span>
        <span class="n">${esc(c.name)}</span>${n ? pill(String(n), 'p-orange') : ''}${c.status === 'bad' ? '<span title="Last test failed" style="color:var(--bad)">●</span>' : ''}</div>${open ? `<div>${body}</div>` : ''}`;
  }).join('') || `<div class="empty small">No data connections yet.${isAdmin() ? ' Add one under <a href="#/admin/connections">Data connections</a>.' : ''}</div>`;

  const selC = D.sel && connById(D.sel.conn_id);
  afterRender = () => { D.docs.forEach((html, i) => { const f = document.querySelector(`iframe[data-doc="${i}"]`); if (f) f.srcdoc = html; }); const m = $('#devmsgs'); if (m) m.scrollTop = 1e6; const q = $('#devq'); if (q && !q.disabled) q.focus(); };
  return `
  <div class="hero"><div><h1>Developer mode</h1><div class="muted">Pick data from one or many connections, describe a report or dashboard, and the agent builds it and publishes it when you approve.</div></div>
    <button class="btn sm" onclick="devReset()">New conversation</button></div>
  <div class="dev">
    <div class="card schema">
      <div class="schema-head"><b>Data sources</b><div class="small muted">Tick objects from any connection. Mixing databases is fine.</div></div>
      <div class="tree">${tree}</div>
      ${D.sel ? `<div class="cols">
        <div class="row" style="justify-content:space-between;margin-bottom:6px"><div><b class="small">${esc(D.sel.name)}</b><div class="small muted">${esc(selC ? selC.name : '')}</div></div></div>
        ${D.cols === null ? '<div class="small muted"><span class="spin"></span>Loading columns…</div>' : D.cols.error ? `<div class="small" style="color:var(--bad)">${esc(D.cols.error)}</div>` :
          `<table>${D.cols.map((c) => `<tr><td>${esc(c.name)}</td><td class="muted">${esc(c.type)}</td><td>${c.key ? pill(c.key, c.key === 'PK' ? 'p-orange' : c.key === 'FK' ? 'p-blue' : 'p-grey') : ''}</td></tr>`).join('')}</table>`}
      </div>` : '<div class="cols small muted">Click a name to see its columns; tick the box to use it.</div>'}
    </div>
    <div class="card devchat">
      <div class="bot-head"><b>⌘ Developer agent</b><div class="small muted">${esc(S.state.model || 'No AI model connected')} · read-only access to the ticked objects</div></div>
      <div class="devmsgs" id="devmsgs">${D.msgs.length ? D.msgs.map(devMsg).join('') : `<div class="msg b">Tick the tables, views or procedures you want on the left (from one database or several), then tell me what to build. I'll ask whether it should be a report or a dashboard, show you a preview, and publish it when you say so.</div>`}</div>
      ${D.picked.length ? `<div class="ctx"><span class="small muted">Using:</span>${D.picked.map((o, i) => `<span class="ctxchip">${esc(o.name)} <span class="muted">· ${esc((connById(o.conn_id) || {}).name || '?')}</span>${busyNow ? '' : `<a onclick="D.picked.splice(${i},1);route()" title="Remove">×</a>`}</span>`).join('')}${busyNow ? '' : '<a class="small" onclick="D.picked=[];route()">Clear</a>'}</div>` : ''}
      <form class="compose" onsubmit="event.preventDefault(); devSend()">
        <input id="devq" value="${esc(D.text)}" oninput="D.text=this.value" placeholder="${D.stage === 'preview' ? 'Ask for a change, e.g. “add a total row”…' : 'Describe the report or dashboard…'}" autocomplete="off" ${busyNow ? 'disabled' : ''}>
        <button class="btn accent" ${busyNow ? 'disabled' : ''}>Send</button></form>
    </div>
  </div>`;
}
const devMsg = (m, i) => `<div class="msg ${m.who} ${m.wide ? 'wide' : ''}">${m.html}${i === D.msgs.length - 1 && m.actions ? `<div class="agent-actions">${m.actions}</div>` : ''}</div>`;
function devSay(m) { D.msgs.push(m); route(); }
async function toggleConn(id) {
  D.open[id] = !D.open[id];
  route();
  if (D.open[id] && !D.schema[id]) {
    try { D.schema[id] = await api(`/api/connections/${id}/schema`); } catch (e) { D.schema[id] = { error: e.message }; }
    route();
  }
}
function toggleGroup(gk) { D.open[gk] = D.open[gk] === false; route(); }
async function selectObj(el) {
  D.sel = { conn_id: Number(el.dataset.c), kind: el.dataset.k, name: el.dataset.n }; D.cols = null; route();
  try { D.cols = (await api(`/api/connections/${D.sel.conn_id}/columns?kind=${D.sel.kind}&name=${encodeURIComponent(D.sel.name)}`)).columns; }
  catch (e) { D.cols = { error: e.message }; }
  route();
}
function pickObj(el) {
  const o = { conn_id: Number(el.dataset.c), kind: el.dataset.k, name: el.dataset.n };
  const i = D.picked.findIndex((p) => p.conn_id === o.conn_id && p.kind === o.kind && p.name === o.name);
  if (i >= 0) D.picked.splice(i, 1); else D.picked.push(o);
  route();
}
function devReset() { Object.assign(D, { picked: [], prompts: [], stage: 'idle', kind: null, draft: null, msgs: [], docs: [], text: '' }); route(); }

function devSend() {
  const raw = D.text.trim(); if (!raw) return;
  if (!S.state.model) { toast('Connect an AI model under LLM & Bot first'); return; }
  D.text = '';
  const clean = stripSkills(raw);
  if (!clean) { D.prompts.push(raw); route(); return; }       // a skill-only message leaves no trace
  D.msgs.push({ who: 'u', html: esc(clean) });
  if (D.stage === 'preview') return devBuild(raw);
  if (!D.picked.length) { devSay({ who: 'b', html: 'Tick at least one table, view or procedure on the left first, then send your request again.' }); D.text = raw; return; }
  D.prompts.push(raw);
  const conns = [...new Set(D.picked.map((p) => p.conn_id))];
  D.stage = 'askKind';
  devSay({ who: 'b', html: `I'll use ${D.picked.length} object(s)${conns.length > 1 ? ` from ${conns.length} different databases` : ''}:\n${D.picked.map((o) => `• <b>${esc(o.name)}</b> <span class="muted">— ${esc((connById(o.conn_id) || {}).name || '?')}</span>`).join('\n')}${conns.length > 1 ? '\nThey can\'t be joined in one SQL statement, so I\'ll query each database read-only and join the results in the page.' : ''}\n\nShould I build this as a <b>Report</b> (tables, printable) or a <b>Dashboard</b> (KPIs and charts)?`,
    actions: `<button class="btn primary sm" onclick="devKind('reports')">▤ Report</button><button class="btn sm" onclick="devKind('dashboards')">◧ Dashboard</button><button class="btn ghost sm" onclick="devReset()">Cancel</button>` });
}
function devKind(kind) {
  D.kind = kind;
  D.msgs.push({ who: 'u', html: kind === 'reports' ? 'Report' : 'Dashboard' });
  devBuild('');
}
async function devBuild(change) {
  const prevStage = D.stage;
  D.stage = 'building';
  devSay({ who: 'b', html: `<span class="spin"></span>${change ? 'Applying your change' : 'Reading the columns, writing the queries, running them read-only and building the page'}… this can take a minute.` });
  try {
    const body = D.draft && change ? { draft_id: D.draft, change } : { kind: D.kind, prompts: D.prompts, objects: D.picked };
    const r = await api('/api/dev/build', { method: 'POST', body });
    D.msgs.pop();
    D.draft = r.draft_id; D.title = r.title; D.docs.push(r.html); D.stage = 'preview';
    const word = r.kind === 'reports' ? 'report' : 'dashboard';
    devSay({ who: 'b', wide: true, html: `Here is a preview of the ${word} <b>“${esc(r.title)}”</b>, run against live data, read-only.
<details class="sqlbox"><summary>Show the ${r.queries.length} quer${r.queries.length === 1 ? 'y' : 'ies'}</summary><pre class="sql">${r.queries.map((q) => `-- ${esc(q.name)} · ${esc(q.connection)}\n${esc(q.sql)}`).join('\n\n')}</pre></details><iframe class="prev" sandbox="allow-scripts" data-doc="${D.docs.length - 1}" title="Preview"></iframe>
Happy with it? I can make it live, or tell me what to change.`,
      actions: `<button class="btn accent sm" onclick="devAskFolder()">✓ Make it live</button><button class="btn sm" onclick="$('#devq').focus()">✎ Ask for changes</button><button class="btn ghost sm" onclick="devReset()">Discard</button>` });
  } catch (e) {
    D.msgs.pop();
    D.stage = prevStage === 'preview' ? 'preview' : 'idle';
    devSay({ who: 'b', html: `⚠ ${esc(e.message)}`, actions: prevStage === 'preview' ? '' : `<button class="btn sm" onclick="D.stage='askKind';devKind(D.kind)">Try again</button>` });
  }
}
async function devAskFolder() {
  D.msgs.push({ who: 'u', html: 'Make it live' });
  D.stage = 'askFolder';
  CAT = await api(`/api/catalog?root=${D.kind}`);
  const word = ROOT_NAME[D.kind];
  devSay({ who: 'b', html: `Where in <b>${word}</b> should it go, and who may open it?
<div class="grid g2" style="gap:0 12px;margin-top:10px">
  <div class="field"><label>Title</label><input id="dv_title" value="${esc(D.title)}"></div>
  <div class="field"><label>Folder in ${word}</label><select id="dv_folder">${folderOptions(null)}</select></div>
  <div class="field"><label>Who can open it</label><select id="dv_vis"><option value="everyone">Everyone signed in</option><option value="restricted">Restricted: named people</option></select></div>
  <div class="field"><label>Refresh the data</label><select id="dv_refresh"><option value="open">Every time it is opened</option><option value="hourly">At most every hour</option><option value="daily">At most once a day</option></select></div>
</div>`,
    actions: `<button class="btn accent sm" onclick="devPublish(this)">Publish live</button><button class="btn ghost sm" onclick="D.stage='preview';devSay({who:'b',html:'OK, not published. Tell me what to change, or press Make it live on the preview.'})">Not yet</button>` });
}
async function devPublish(btn) {
  const body = { draft_id: D.draft, title: val('dv_title') || D.title, folder_id: val('dv_folder') ? Number(val('dv_folder')) : null, visibility: val('dv_vis'), refresh: val('dv_refresh') };
  const where = $('#dv_folder').selectedOptions[0].textContent;
  await busy(btn, async () => {
    const r = await api('/api/dev/publish', { method: 'POST', body });
    D.msgs.push({ who: 'u', html: 'Publish live' });
    D.stage = 'live';
    devSay({ who: 'b', html: `✓ <b>${esc(body.title)}</b> is live in <b>${esc(where)}</b>, open to ${body.visibility === 'everyone' ? 'everyone signed in' : 'the people an administrator grants it to'}. Its queries re-run read-only ${{ open: 'each time it is opened', hourly: 'at most hourly', daily: 'at most daily' }[body.refresh]}, the Assistant can answer from it, and the publish is in the audit log.`,
      actions: `<a class="btn primary sm" href="#/item/${r.id}">Open it</a><button class="btn sm" onclick="devReset()">Build another</button>` });
  });
}

/* ---------------------------------------------------------------- admin: data connections */
let TYPES = null;
async function pageConnections() {
  const d = await api('/api/connections');
  D.conns = d.connections;
  if (!TYPES) TYPES = await api('/api/connection-types');
  return `<div class="hero"><div><h1>Data connections</h1><div class="muted">Databases that reports, dashboards, Developer mode and the Assistant read from. Passwords are stored encrypted and never shown again.</div></div>
    <button class="btn primary" onclick="connModal()">＋ New connection</button></div>
  ${d.connections.length ? `<div class="card" style="padding:6px 8px"><table>
    <tr><th>Name</th><th>Type</th><th>Host / file</th><th>Database</th><th>Status</th><th>Last test</th><th></th></tr>
    ${d.connections.map((c) => `<tr>
      <td><b>${esc(c.name)}</b>${c.last_error ? `<div class="small" style="color:var(--bad)">${esc(c.last_error)}</div>` : ''}</td>
      <td>${esc(c.label)}</td><td class="small muted">${esc(c.host)}</td><td class="small muted">${esc(c.database)}</td>
      <td>${statusPill(c.status)}</td><td class="small muted">${when(c.tested_at)}</td>
      <td class="right"><button class="btn sm" onclick="testConn(${c.id}, this)">Test</button> <button class="btn sm" onclick="connModal(${c.id})">Edit</button></td>
    </tr>`).join('')}</table></div>` : '<div class="card empty">No connections yet. Add the first one.</div>'}
  <p class="small muted" style="margin-top:12px">Give CubixOne a <b>read-only</b> login. It only runs single SELECT queries and opens PostgreSQL sessions read-only, but the database login is the real protection.</p>`;
}
async function testConn(id, btn) {
  await busy(btn, async () => {
    const r = await api(`/api/connections/${id}/test`, { method: 'POST' });
    toast(r.ok ? `✓ Connected in ${r.ms} ms${r.version ? ' · version ' + r.version : ''}` : `✗ ${r.error}`);
    route();
  });
}
const FIELD = { host: ['Host', 'db.example.com'], port: ['Port', ''], database: ['Database', ''], user: ['Username', ''], password: ['Password', ''],
  service: ['Service name', 'ORCLPDB1'], sslmode: ['SSL mode', 'require / verify-full / prefer / disable'], schema: ['Schema (optional)', 'default schema'],
  path: ['Database file path (inside the container)', '/app/data/sample.db'] };
let CONN = null;
function connModal(id) {
  const c = id ? D.conns.find((x) => x.id === id) : null;
  CONN = { id: id || null, type: c ? c.type : 'sqlserver', name: c ? c.name : '', cfg: c ? { ...c.config } : {}, hasPw: c ? c.has_password : false };
  drawConnModal();
}
function drawConnModal() {
  const t = TYPES.types[CONN.type];
  const fields = t.fields.map((f) => {
    const [label, ph] = FIELD[f];
    const v = CONN.cfg[f] ?? (f === 'port' && t.port ? t.port : '');
    const isPw = f === 'password';
    return `<div class="field"><label>${label}</label><input type="${isPw ? 'password' : 'text'}" data-f="${f}" value="${esc(v)}" placeholder="${esc(isPw && CONN.hasPw ? 'Leave blank to keep the stored password' : ph)}" oninput="CONN.cfg[this.dataset.f]=this.value"></div>`;
  }).join('');
  modal(`<h2>${CONN.id ? 'Edit' : 'New'} data connection</h2>
    <div class="field"><label>Type</label></div>
    <div class="grid g3" style="gap:8px;margin-bottom:16px">${Object.entries(TYPES.types).map(([k, x]) => `
      <div class="conn-type ${CONN.type === k ? 'on' : ''}" onclick="CONN.type='${k}';CONN.cfg={};drawConnModal()"><span class="dbic" style="background:#1B4DDB">${esc(x.abbr)}</span><span class="small"><b>${esc(x.label)}</b></span></div>`).join('')}
      ${TYPES.coming_soon.map((x) => `<div class="conn-type" style="opacity:.45;cursor:default" title="Coming soon"><span class="dbic" style="background:#2A3550">··</span><span class="small"><b>${esc(x)}</b><br><span class="muted">coming soon</span></span></div>`).join('')}</div>
    <div class="field"><label>Connection name</label><input value="${esc(CONN.name)}" placeholder="e.g. Sales warehouse" oninput="CONN.name=this.value"></div>
    <div class="grid g2" style="gap:0 14px">${fields}</div>
    <div id="connTest"></div>
    <div class="row" style="justify-content:space-between;margin-top:14px">
      <div class="row"><button class="btn" onclick="testDraftConn(this)">⚡ Test connection</button>${CONN.id ? `<button class="btn" style="color:var(--bad)" onclick="deleteConn(this)">Delete</button>` : ''}</div>
      <div class="row"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveConn(this)">Save</button></div>
    </div>`);
}
async function testDraftConn(btn) {
  $('#connTest').innerHTML = '<div class="test run"><span class="spin"></span>Connecting…</div>';
  await busy(btn, async () => {
    const r = await api('/api/connections/test', { method: 'POST', body: { type: CONN.type, config: CONN.cfg, id: CONN.id } });
    $('#connTest').innerHTML = r.ok ? `<div class="test ok">✓ Connected in ${r.ms} ms${r.version ? ' · server version ' + esc(r.version) : ''}</div>` : `<div class="test bad">✗ ${esc(r.error)}</div>`;
  });
}
async function saveConn(btn) {
  if (!CONN.name.trim()) { toast('Give the connection a name'); return; }
  await busy(btn, async () => {
    const body = { name: CONN.name, type: CONN.type, config: CONN.cfg };
    if (CONN.id) await api(`/api/connections/${CONN.id}`, { method: 'PATCH', body }); else await api('/api/connections', { method: 'POST', body });
    closeModal(); D.conns = null; D.schema = {}; toast('Connection saved'); route();
  });
}
async function deleteConn(btn) {
  if (!confirm('Delete this connection? Items built on it will stop refreshing.')) return;
  await busy(btn, async () => { await api(`/api/connections/${CONN.id}`, { method: 'DELETE' }); closeModal(); D.conns = null; toast('Connection deleted'); route(); });
}

/* ---------------------------------------------------------------- admin: LLM & Bot */
let PROVIDERS = null, MODEL = null, SETTINGS = null;
async function pageLLM() {
  const [d, st] = await Promise.all([api('/api/llms'), api('/api/settings')]);
  if (!PROVIDERS) PROVIDERS = (await api('/api/llm-providers')).providers;
  SETTINGS = st.settings;
  return `<div class="hero"><div><h1>LLM &amp; Bot</h1><div class="muted">Connect your own model with your own API key. Keys are stored encrypted and used only by the server.</div></div>
    <button class="btn primary" onclick="modelModal()">＋ Connect a model</button></div>
  ${d.models.length ? `<div class="card" style="padding:6px 8px;margin-bottom:18px"><table>
    <tr><th>Name</th><th>Provider</th><th>Model</th><th>API key</th><th>Status</th><th></th></tr>
    ${d.models.map((m) => `<tr>
      <td><b>${esc(m.name)}</b> ${m.is_default ? pill('Default', 'p-orange') : ''}${m.last_error ? `<div class="small" style="color:var(--bad)">${esc(m.last_error)}</div>` : ''}</td>
      <td>${esc(m.provider_label)}</td><td class="small">${esc(m.model)}</td><td class="small muted">${esc(m.key_hint || '—')}</td>
      <td>${statusPill(m.status)}</td>
      <td class="right"><button class="btn sm" onclick="testModel(${m.id}, this)">Test</button>
        ${m.is_default ? '' : `<button class="btn sm" onclick="makeDefault(${m.id}, this)">Make default</button>`}
        <button class="btn sm" onclick='modelModal(${JSON.stringify(m).replace(/'/g, '&#39;')})'>Edit</button></td>
    </tr>`).join('')}</table></div>` : '<div class="card empty" style="margin-bottom:18px">No model connected yet. The Assistant and Developer mode need one.</div>'}
  <div class="grid g2">
    <div class="card"><h2>Assistant</h2>
      <label class="row small" style="gap:8px;margin-bottom:12px"><input type="checkbox" id="bot_on" ${SETTINGS.bot_enabled === '1' ? 'checked' : ''}> Show the Assistant to everyone</label>
      <div class="field"><label>Instructions given to the model</label><textarea id="bot_prompt" rows="5">${esc(SETTINGS.bot_prompt)}</textarea></div>
      <button class="btn primary" onclick="saveBot(this)">Save</button>
    </div>
    <div class="card"><h2>How it answers</h2>
      <ol class="muted" style="line-height:1.7;padding-left:18px;margin:0">
        <li>Uploaded pages and AI-built items are turned into searchable text.</li>
        <li>A question is matched only against items the person asking may open.</li>
        <li>The best passages and the question go to the default model.</li>
        <li>The answer names its sources, and every question is audited.</li>
      </ol>
      <div class="banner" style="margin-top:14px">Developer mode uses the same default model to write queries and pages.</div>
    </div>
  </div>`;
}
const presetUrl = (k) => { const u = (PROVIDERS[k] || {}).base_url || ''; return u === 'required' ? '' : u; };
function modelModal(m) {
  MODEL = m ? { ...m, api_key: '' } : { id: null, provider: 'anthropic', name: '', model: 'claude-opus-5-5', base_url: '', api_key: '', api_version: '', key_hint: '' };
  drawModelModal();
}
function pickProvider(k) {
  MODEL.provider = k; MODEL.model = PROVIDERS[k].models[0] || ''; MODEL.base_url = presetUrl(k); drawModelModal();
}
function drawModelModal() {
  const p = PROVIDERS[MODEL.provider] || PROVIDERS.compat;
  const groups = [
    ['Hosted providers', Object.keys(PROVIDERS).filter((k) => !['ollama', 'lmstudio', 'compat'].includes(k))],
    ['Self-hosted', ['ollama', 'lmstudio']],
    ['Any other model', ['compat']],
  ];
  const azure = MODEL.provider === 'azure';
  modal(`<h2>${MODEL.id ? 'Edit model' : 'Connect a model'}</h2>
    <p class="small muted">Any model can be connected. Pick a provider to pre-fill its address, or choose <b>Custom</b> for any OpenAI-compatible API. Every field stays editable.</p>
    <div class="field"><label>Provider</label><select onchange="pickProvider(this.value)">
      ${groups.map(([g, ks]) => `<optgroup label="${g}">${ks.filter((k) => PROVIDERS[k]).map((k) => `<option value="${k}" ${k === MODEL.provider ? 'selected' : ''}>${esc(PROVIDERS[k].label)}</option>`).join('')}</optgroup>`).join('')}</select></div>
    <div class="field"><label>Display name</label><input value="${esc(MODEL.name)}" placeholder="${esc(p.label)}" oninput="MODEL.name=this.value"></div>
    <div class="field"><label>${azure ? 'Azure endpoint' : 'Base URL'}${MODEL.provider === 'anthropic' ? ' (optional)' : ''}</label>
      <input value="${esc(MODEL.base_url)}" placeholder="${esc(presetUrl(MODEL.provider) || (MODEL.provider === 'anthropic' ? 'Anthropic API (default)' : 'https://api.example.com/v1'))}" oninput="MODEL.base_url=this.value"></div>
    ${p.note ? `<p class="small muted">${esc(p.note)}</p>` : ''}
    <div class="field"><label>${azure ? 'Deployment name' : 'Model'}</label>
      <input list="model-suggestions" value="${esc(MODEL.model)}" placeholder="Model name exactly as the provider lists it" oninput="MODEL.model=this.value">
      <datalist id="model-suggestions">${p.models.map((x) => `<option value="${esc(x)}">`).join('')}</datalist></div>
    ${azure ? `<div class="field"><label>API version</label><input value="${esc(MODEL.api_version || '2024-10-21')}" oninput="MODEL.api_version=this.value"></div>` : ''}
    <div class="field"><label>API key${p.key ? '' : ' (optional)'}</label><input type="password" placeholder="${MODEL.key_hint ? 'Stored (' + esc(MODEL.key_hint) + '). Leave blank to keep it.' : p.key ? 'Paste the key. It is stored encrypted.' : 'Only if your endpoint needs one'}" oninput="MODEL.api_key=this.value"></div>
    <div id="modelTest"></div>
    <div class="row" style="justify-content:space-between;margin-top:14px">
      <div class="row"><button class="btn" onclick="testDraftModel(this)">⚡ Test</button>${MODEL.id ? `<button class="btn" style="color:var(--bad)" onclick="deleteModel(this)">Delete</button>` : ''}</div>
      <div class="row"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveModel(this)">Save</button></div>
    </div>`);
}
const modelBody = () => ({ name: MODEL.name, provider: MODEL.provider, model: MODEL.model, base_url: MODEL.base_url, api_key: MODEL.api_key, api_version: MODEL.api_version || '' });
async function testDraftModel(btn) {
  $('#modelTest').innerHTML = '<div class="test run"><span class="spin"></span>Sending a short test prompt…</div>';
  await busy(btn, async () => {
    const r = await api('/api/llms/test', { method: 'POST', body: { ...modelBody(), id: MODEL.id } });
    $('#modelTest').innerHTML = r.ok ? `<div class="test ok">✓ The model replied in ${r.ms} ms: “${esc(r.reply)}”</div>` : `<div class="test bad">✗ ${esc(r.error)}</div>`;
  });
}
async function saveModel(btn) {
  await busy(btn, async () => {
    if (MODEL.id) await api(`/api/llms/${MODEL.id}`, { method: 'PATCH', body: modelBody() }); else await api('/api/llms', { method: 'POST', body: modelBody() });
    closeModal(); S.state = null; await loadState(); toast('Model saved'); route();
  });
}
async function deleteModel(btn) {
  if (!confirm('Delete this model and its stored key?')) return;
  await busy(btn, async () => { await api(`/api/llms/${MODEL.id}`, { method: 'DELETE' }); closeModal(); S.state = null; await loadState(); toast('Model deleted'); route(); });
}
async function testModel(id, btn) {
  await busy(btn, async () => { const r = await api(`/api/llms/${id}/test`, { method: 'POST' }); toast(r.ok ? `✓ Replied in ${r.ms} ms` : `✗ ${r.error}`); route(); });
}
async function makeDefault(id, btn) {
  await busy(btn, async () => { await api(`/api/llms/${id}/default`, { method: 'POST' }); S.state = null; await loadState(); toast('Default model changed'); route(); });
}
async function saveBot(btn) {
  await busy(btn, async () => {
    await api('/api/settings', { method: 'PUT', body: { bot_enabled: $('#bot_on').checked ? '1' : '0', bot_prompt: val('bot_prompt') } });
    S.state = null; await loadState(); toast('Assistant settings saved'); route();
  });
}

/* ---------------------------------------------------------------- admin: users */
let USERS = [];
async function pageUsers() {
  const d = await api('/api/users');
  USERS = d.users;
  const role = (r) => r === 'admin' ? pill('Admin', 'p-orange') : r === 'developer' ? pill('Developer', 'p-cream') : pill('Viewer', 'p-blue');
  return `<div class="hero"><div><h1>Users &amp; access</h1><div class="muted">Admins manage everything; developers build and publish with Developer mode; viewers open what is shared with them.</div></div>
    <button class="btn primary" onclick="userModal()">＋ Add user</button></div>
  <div class="card" style="padding:6px 8px"><table>
    <tr><th>User</th><th>Role</th><th>Status</th><th>Granted</th><th>Last sign-in</th><th></th></tr>
    ${d.users.map((u) => `<tr>
      <td><b>${esc(u.full_name || u.username)}</b><div class="small muted">${esc(u.username)}${u.email ? ' · ' + esc(u.email) : ''}</div></td>
      <td>${role(u.role)}</td>
      <td>${!u.is_active ? pill('Disabled', 'p-grey') : u.locked ? pill('Locked', 'p-red') : u.must_change ? pill('Must change password', 'p-cream') : pill('Active', 'p-teal')}</td>
      <td class="small">${u.grants.length ? esc(u.grants.join(', ')) : '<span class="muted">—</span>'}</td>
      <td class="small muted">${when(u.last_login_at)}</td>
      <td class="right"><button class="btn sm" onclick="userModal(${u.id})">Edit</button></td>
    </tr>`).join('')}</table></div>
  <p class="small muted" style="margin-top:12px">Grant restricted items and folders from the item's or folder's Manage dialog.</p>`;
}
function userModal(id) {
  const u = id ? USERS.find((x) => x.id === id) : null;
  modal(`<h2>${u ? 'Edit ' + esc(u.username) : 'Add user'}</h2>
    <div class="grid g2" style="gap:0 14px">
      ${u ? '' : '<div class="field"><label>Username</label><input id="um_user" autocomplete="off"></div>'}
      <div class="field"><label>Full name</label><input id="um_name" value="${esc(u ? u.full_name : '')}"></div>
      <div class="field"><label>Email</label><input id="um_email" value="${esc(u ? u.email : '')}"></div>
      <div class="field"><label>Role</label><select id="um_role">${['viewer', 'developer', 'admin'].map((r) => `<option value="${r}" ${u && u.role === r ? 'selected' : ''}>${{ viewer: 'Viewer', developer: 'Developer (builds with Developer mode)', admin: 'Admin' }[r]}</option>`).join('')}</select></div>
      <div class="field"><label>${u ? 'New temporary password (optional)' : 'Temporary password'}</label><input id="um_pass" type="password" autocomplete="new-password"></div>
    </div>
    <p class="small muted">With a temporary password, the person must choose their own at the next sign-in.</p>
    ${u ? `<div class="row" style="margin-bottom:8px"><label class="row small" style="gap:8px"><input type="checkbox" id="um_active" ${u.is_active ? 'checked' : ''}> Account enabled</label>${u.locked ? '<label class="row small" style="gap:8px"><input type="checkbox" id="um_unlock" checked> Unlock now</label>' : ''}</div>` : ''}
    <div class="row" style="justify-content:space-between;margin-top:10px">
      <div>${u && u.id !== S.user.id ? `<button class="btn" style="color:var(--bad)" onclick="deleteUser(${u.id}, this)">Delete</button>` : ''}</div>
      <div class="row"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary" onclick="saveUser(${u ? u.id : 'null'}, this)">Save</button></div></div>`);
}
async function saveUser(id, btn) {
  await busy(btn, async () => {
    if (!id) {
      await api('/api/users', { method: 'POST', body: { username: val('um_user'), full_name: val('um_name'), email: val('um_email'), role: val('um_role'), password: val('um_pass') } });
    } else {
      await api(`/api/users/${id}`, { method: 'PATCH', body: { full_name: val('um_name'), email: val('um_email'), role: val('um_role'), is_active: $('#um_active').checked, unlock: !!($('#um_unlock') && $('#um_unlock').checked) } });
      if (val('um_pass')) await api(`/api/users/${id}/password`, { method: 'POST', body: { password: val('um_pass') } });
    }
    closeModal(); toast('Saved'); route();
  });
}
async function deleteUser(id, btn) {
  if (!confirm('Delete this account? Their grants are removed too.')) return;
  await busy(btn, async () => { await api(`/api/users/${id}`, { method: 'DELETE' }); closeModal(); toast('Deleted'); route(); });
}

/* ---------------------------------------------------------------- admin: audit, settings */
async function pageAudit() {
  const d = await api('/api/audit?limit=500');
  const oc = (o) => o === 'allow' ? pill('allow', 'p-teal') : o === 'deny' ? pill('deny', 'p-orange') : pill('error', 'p-red');
  return `<div class="hero"><div><h1>Audit log</h1><div class="muted">Sign-ins, views, refusals, uploads, publishes, connection and model tests, Assistant questions. Newest first; never edited.</div></div>
    <button class="btn" onclick="exportAudit()">⇩ Export CSV</button></div>
  <div class="card" style="padding:6px 8px"><table>
    <tr><th>Time</th><th>User</th><th>Action</th><th>Item / target</th><th>Outcome</th><th>Detail</th><th>IP</th></tr>
    ${d.rows.map((r) => `<tr><td class="small muted" style="white-space:nowrap">${when(r.at)}</td><td>${esc(r.user)}</td><td><code>${esc(r.action)}</code></td><td class="small">${esc(r.target)}</td><td>${oc(r.outcome)}</td><td class="small muted">${esc(r.detail)}</td><td class="small muted">${esc(r.ip)}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No events yet.</td></tr>'}
  </table></div>`;
}
async function exportAudit() {
  const d = await api('/api/audit?limit=2000');
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = ['time,user,action,target,outcome,detail,ip', ...d.rows.map((r) => [r.at, r.user, r.action, r.target, r.outcome, r.detail, r.ip].map(q).join(','))].join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'cubixone-audit.csv'; a.click();
}

async function pageSettings() {
  const d = await api('/api/settings');
  const s = d.settings;
  const num = (k, label) => `<div class="field"><label>${label}</label><input type="number" min="1" data-k="${k}" value="${esc(s[k])}"></div>`;
  const cols = [['2F7BFF', 'Neon blue'], ['1B4DDB', 'Deep blue'], ['00C2FF', 'Neon cyan'], ['05070D', 'Black'], ['0A0F1C', 'Night'], ['141C2E', 'Graphite'], ['8B97AD', 'Grey'], ['C9D1DC', 'Metallic']];
  return `<div class="hero"><div><h1>Settings</h1><div class="muted">Branding, security and this installation.</div></div></div>
  <div class="grid g2">
    <div class="card"><h2>Branding</h2>
      <div class="field"><label>Portal name</label><input data-k="brand" value="${esc(s.brand)}"></div>
      <label class="small muted" style="font-weight:600">Colour scheme</label>
      <div class="grid g4" style="gap:6px;margin-top:6px">${cols.map(([c, n]) => `<div class="swatch" style="background:${c === 'C9D1DC' ? 'var(--metal)' : '#' + c};color:${['00C2FF', '8B97AD', 'C9D1DC'].includes(c) ? '#05070D' : '#E6EBF3'}">${n}<br>#${c}</div>`).join('')}</div>
    </div>
    <div class="card"><h2>Security</h2>
      <div class="grid g2" style="gap:0 14px">
        ${num('session_hours', 'Session length (hours)')}${num('idle_minutes', 'Idle timeout (minutes)')}
        ${num('password_min_length', 'Password minimum length')}${num('login_max_failures', 'Failed sign-ins before lock')}
        ${num('login_lock_minutes', 'Lock duration (minutes)')}${num('max_upload_mb', 'Max upload (MB)')}
        ${num('max_package_mb', 'Max .zip size unpacked (MB)')}
      </div>
    </div>
    <div class="card"><h2>This installation</h2>
      <table><tr><td class="muted">Version</td><td>${esc(S.state.version)}</td></tr>
        <tr><td class="muted">User store</td><td>${esc(d.user_store)}</td></tr>
        <tr><td class="muted">Skills folder</td><td><code>${esc(d.skills_dir)}</code></td></tr></table>
    </div>
  </div>
  <div style="margin-top:16px"><button class="btn primary" onclick="saveSettings(this)">Save settings</button></div>`;
}
async function saveSettings(btn) {
  const body = {};
  document.querySelectorAll('[data-k]').forEach((e) => { body[e.dataset.k] = e.value; });
  await busy(btn, async () => { await api('/api/settings', { method: 'PUT', body }); S.state = null; await loadState(); toast('Settings saved'); route(); });
}

route();
