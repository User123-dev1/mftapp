/* Password Vault web interface. Uses only the vault's REST API (/api/...).
 * Security notes: the session token lives in sessionStorage (this tab only, gone when it closes);
 * all server data is inserted with textContent (never as HTML); revealed values are hidden again
 * after 60 seconds or when the tab is hidden, and copied values are cleared from the clipboard
 * after 30 seconds. The page is served with a strict Content-Security-Policy. */
'use strict';

const S = { token: null, me: null, folders: [], templates: [], groups: [], users: null, pendingReviews: 0 };
const REVEAL_SECONDS = 60, CLIPBOARD_SECONDS = 30;
const LEVELS = ['view', 'edit', 'owner'];
const SETTINGS = [
  ['require_checkout', 'Must be checked out before use (one person at a time)'],
  ['rotate_on_checkin', 'Change the password automatically at check-in'],
  ['require_comment', 'A reason is required to view it'],
  ['require_approval', 'Viewing needs an approved access request'],
];
let revealTimer = null;

// ---------------------------------------------------------------- helpers

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const app = () => document.getElementById('app');
function mount(...nodes) { const a = app(); a.replaceChildren(...nodes.flat()); }
function toast(msg, bad) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.className = ''; }, 3500);
}
function when(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  return isNaN(d) ? iso : d.toLocaleString();
}
function errText(e) { return e && e.message ? e.message : String(e); }
function flash(kind, text) { return h('div', { class: 'flash ' + kind }, text); }
function fieldVal(form, name) { const el = form.elements[name]; return el ? el.value.trim() : ''; }
function userName(id) {
  if (S.me && id === S.me.id) return 'you';
  const u = S.users && S.users.find(x => x.id === id);
  return u ? (u.display_name || u.username) : 'user #' + id;
}
function groupName(id) { const g = S.groups.find(x => x.id === id); return g ? g.name : 'group #' + id; }
function folderName(id) { const f = S.folders.find(x => x.id === id); return f ? f.name : 'folder #' + id; }
function folderPath(id) {
  const parts = []; let f = S.folders.find(x => x.id === id);
  while (f) { parts.unshift(f.name); f = S.folders.find(x => x.id === f.parent_id); }
  return parts.join(' / ') || folderName(id);
}
function canEditFolder(f) { return f && (f.my_permission === 'edit' || f.my_permission === 'owner'); }
function route() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), params: new URLSearchParams(query || '') };
}
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }

// ---------------------------------------------------------------- API

async function api(method, path, body) {
  try { return await apiCall(method, path, body); }
  catch (e) {
    if (e instanceof TypeError) throw new Error('Could not reach the vault. Reload the page (your Microsoft sign-in may have expired) and try again.');
    throw e;
  }
}
async function apiCall(method, path, body) {
  const headers = { 'Accept': 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  // credentials 'same-origin': behind Entra Application Proxy the browser must send Entra's own sign-in
  // cookie with every request. The vault itself only uses the bearer token, never cookies.
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body),
                                credentials: 'same-origin', cache: 'no-store' });
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch (_) { /* not JSON */ }
  if (!r.ok) {
    if (r.status === 401 && S.token && !path.startsWith('/api/auth/')) {
      signedOut('Your session has ended. Please sign in again.');
      throw new Error('session ended');
    }
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => (d.loc ? d.loc.slice(-1)[0] + ': ' : '') + d.msg).join('; ');
    const err = new Error(msg || ('Request failed (' + r.status + ')'));
    err.status = r.status;
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------- session

function storeToken(t) {
  S.token = t;
  try { t ? sessionStorage.setItem('vault_token', t) : sessionStorage.removeItem('vault_token'); } catch (_) { /* private mode */ }
}
function signedOut(message) {
  storeToken(null); S.me = null; S.users = null;
  hideRevealed();
  renderLogin(message);
}
async function signOut() {
  try { await api('POST', '/api/auth/logout'); } catch (_) { /* already gone */ }
  signedOut('You have signed out.');
}
async function startSession(token) {
  storeToken(token);
  await loadBasics();
  if (!location.hash || location.hash === '#') location.hash = '#/secrets'; else render();
}
async function loadBasics() {
  const [me, folders, templates, groups] = await Promise.all([
    api('GET', '/api/auth/me'), api('GET', '/api/folders'), api('GET', '/api/templates'), api('GET', '/api/groups')]);
  Object.assign(S, { me, folders, templates, groups });
  S.users = me.role === 'administrator' ? await api('GET', '/api/users') : null;
  try {
    const reqs = await api('GET', '/api/access-requests');
    S.pendingReviews = reqs.filter(r => r.status === 'pending' && r.requester !== me.username).length;
  } catch (_) { S.pendingReviews = 0; }
}
const isAdmin = () => S.me && S.me.role === 'administrator';

// ---------------------------------------------------------------- sign-in

async function renderLogin(message) {
  location.hash = '';
  let providers = [];
  try { providers = await api('GET', '/api/auth/providers'); } catch (_) { /* none */ }
  const form = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    const btn = form.querySelector('button'); btn.disabled = true;
    try {
      const res = await api('POST', '/api/auth/login', { username: fieldVal(form, 'username'), password: form.elements.password.value });
      if (res.mfa_required) return renderMfa(res.mfa_token);
      await startSession(res.access_token);
    } catch (e) { status.replaceChildren(flash('bad', errText(e))); btn.disabled = false; }
  } },
    h('label', {}, 'User name', h('input', { name: 'username', autocomplete: 'username', required: true, autofocus: true })),
    h('label', {}, 'Password', h('input', { name: 'password', type: 'password', autocomplete: 'current-password', required: true })),
    h('button', { class: 'btn', type: 'submit' }, 'Sign in'));
  const status = h('div', {}, message ? flash('info', message) : null);
  mount(h('main', {}, h('div', { class: 'card narrow' },
    h('img', { class: 'hero-key', src: '/ui/vault.svg', alt: '' }),
    h('h1', { class: 'center' }, 'Password Vault'),
    status,
    providers.length ? h('div', { class: 'sso' }, providers.map(p =>
      h('a', { href: p.login_url }, 'Sign in with ' + (p.name.toLowerCase() === 'entra' ? 'Microsoft (Entra ID)' : p.name)))) : null,
    providers.length ? h('div', { class: 'divider' }, 'or with a vault account') : null,
    form)));
}

function renderMfa(mfaToken) {
  const status = h('div');
  const form = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    try {
      const res = await api('POST', '/api/auth/mfa/verify', { mfa_token: mfaToken, code: fieldVal(form, 'code') });
      await startSession(res.access_token);
    } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } },
    h('label', {}, 'Code from your authenticator app',
      h('input', { name: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', required: true, autofocus: true })),
    h('button', { class: 'btn', type: 'submit' }, 'Verify'));
  mount(h('main', {}, h('div', { class: 'card narrow' }, h('h1', {}, 'Two-step verification'), status, form)));
}

// ---------------------------------------------------------------- layout

function shell(active, ...content) {
  // Clicking the page you're on reloads it (e.g. to see newly approved requests).
  const link = (hash, label, key, extra) => h('a', { href: hash, class: active === key ? 'active' : null,
    onclick: ev => { if (location.hash === hash) { ev.preventDefault(); render(); } } }, label, extra);
  return [
    h('header', { class: 'topbar' },
      h('a', { class: 'brand', href: '#/secrets' }, h('img', { src: '/ui/vault.svg', alt: '' }), 'Password Vault'),
      h('nav', { class: 'nav' },
        link('#/secrets', 'Secrets', 'secrets'),
        link('#/requests', 'Access requests', 'requests', S.pendingReviews ? h('span', { class: 'count' }, S.pendingReviews) : null),
        isAdmin() ? link('#/admin/users', 'Administration', 'admin') : null,
        link('#/account', 'My account', 'account')),
      h('div', { class: 'who' }, S.me.display_name || S.me.username,
        isAdmin() ? h('span', { class: 'badge info' }, 'admin') : null,
        h('button', { type: 'button', onclick: signOut }, 'Sign out'))),
    h('main', {}, content)];
}

async function render() {
  hideRevealed();
  if (!S.token) return renderLogin();
  const { parts, params } = route();
  try {
    if (parts[0] === 'secret' && parts[1] === 'new') return await viewNewSecret(params);
    if (parts[0] === 'secret' && parts[1]) return await viewSecret(Number(parts[1]), parts[2] || 'details');
    if (parts[0] === 'requests') return await viewRequests();
    if (parts[0] === 'account') return await viewAccount();
    if (parts[0] === 'admin' && isAdmin()) return await viewAdmin(parts[1] || 'users');
    return await viewSecrets(params);
  } catch (e) {
    if (S.token) mount(shell('', flash('bad', errText(e))));
  }
}

// ---------------------------------------------------------------- secrets list

function folderTree(parentId, selected) {
  const kids = S.folders.filter(f => (f.parent_id || null) === parentId);
  if (!kids.length) return null;
  return h('ul', { class: 'tree' }, kids.map(f => h('li', {},
    h('a', { href: '#/secrets?folder=' + f.id, class: selected === f.id ? 'active' : null }, '📁 ' + f.name),
    folderTree(f.id, selected))));
}

async function viewSecrets(params) {
  const folderId = params.get('folder') ? Number(params.get('folder')) : null;
  const q = params.get('q') || '';
  const qs = new URLSearchParams();
  if (q) qs.set('q', q);
  if (folderId) qs.set('folder_id', folderId);
  const secrets = await api('GET', '/api/secrets' + (qs.toString() ? '?' + qs : ''));
  const folder = S.folders.find(f => f.id === folderId);
  const search = h('form', { class: 'grow', onsubmit: ev => {
    ev.preventDefault();
    const p = new URLSearchParams(); const v = fieldVal(ev.target, 'q');
    if (v) p.set('q', v); if (folderId) p.set('folder', folderId);
    go('#/secrets' + (p.toString() ? '?' + p : ''));
  } }, h('input', { name: 'q', value: q, placeholder: 'Search secrets by name…', type: 'search' }));

  const newFolder = h('form', { class: 'toolbar', hidden: true, onsubmit: async ev => {
    ev.preventDefault();
    try {
      const f = await api('POST', '/api/folders', { name: fieldVal(ev.target, 'name'), parent_id: folderId || null });
      S.folders = await api('GET', '/api/folders');
      go('#/secrets?folder=' + f.id);
    } catch (e) { toast(errText(e), true); }
  } }, h('input', { name: 'name', placeholder: folder ? 'New folder inside ' + folder.name : 'New top-level folder', required: true, class: 'grow' }),
    h('button', { class: 'btn small', type: 'submit' }, 'Create'));
  const canAddFolder = folder ? canEditFolder(folder) : isAdmin();
  const canAddSecret = folder ? canEditFolder(folder) : S.folders.some(canEditFolder);

  const list = secrets.length ? h('table', {},
    h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', {}, 'Folder'), h('th', {}, 'Type'))),
    h('tbody', {}, secrets.map(s => h('tr', { class: 'click', onclick: () => go('#/secret/' + s.id) },
      h('td', {}, h('a', { href: '#/secret/' + s.id }, s.name)),
      h('td', { class: 'muted' }, folderPath(s.folder_id)),
      h('td', { class: 'muted' }, (S.templates.find(t => t.id === s.template_id) || {}).name || '')))))
    : h('div', { class: 'empty' }, q ? 'No secrets match your search.' :
        S.folders.length ? 'No secrets here yet.' : 'You don\'t have access to any folders yet. Ask a vault administrator.');

  mount(shell('secrets', h('div', { class: 'split' },
    h('div', { class: 'card' },
      h('div', { class: 'toolbar' }, h('strong', { class: 'grow' }, 'Folders'),
        canAddFolder ? h('button', { class: 'btn ghost small', type: 'button', title: 'New folder',
          onclick: () => { newFolder.hidden = !newFolder.hidden; } }, '+ Folder') : null),
      newFolder,
      h('ul', { class: 'tree' }, h('li', {}, h('a', { href: '#/secrets', class: folderId ? null : 'active' }, 'All secrets'))),
      folderTree(null, folderId) || (S.folders.length ? null : h('p', { class: 'muted small' }, 'No folders yet.'))),
    h('div', { class: 'card' },
      h('h1', {}, folder ? folderPath(folder.id) : 'All secrets'),
      h('div', { class: 'toolbar' }, search,
        canAddSecret ? h('a', { class: 'btn', href: '#/secret/new' + (folderId ? '?folder=' + folderId : '') }, '+ New secret') : null,
        folder && folder.my_permission === 'owner' ? h('a', { class: 'btn secondary', href: '#/admin/folders?folder=' + folder.id,
          onclick: ev => { if (!isAdmin()) { ev.preventDefault(); editFolderPermissions(folder.id); } } }, 'Folder permissions') : null),
      list))));
}

// ---------------------------------------------------------------- one secret

function hideRevealed() {
  clearTimeout(revealTimer); revealTimer = null;
  document.querySelectorAll('[data-secret-value]').forEach(el => {
    el.textContent = '••••••••'; el.classList.add('masked'); el.removeAttribute('data-secret-value');
  });
  document.querySelectorAll('[data-copy]').forEach(b => { b.disabled = true; b.removeAttribute('data-copy'); });
}
document.addEventListener('visibilitychange', () => { if (document.hidden) hideRevealed(); });

async function copyValue(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast('Copied. The clipboard is cleared in ' + CLIPBOARD_SECONDS + ' seconds.');
    setTimeout(async () => {
      try { if ((await navigator.clipboard.readText()) === value) await navigator.clipboard.writeText(''); }
      catch (_) { try { await navigator.clipboard.writeText(''); } catch (__) { /* page not focused */ } }
    }, CLIPBOARD_SECONDS * 1000);
  } catch (_) { toast('Copying is blocked by the browser here; select the text instead.', true); }
}

async function viewSecret(id, tab) {
  const s = await api('GET', '/api/secrets/' + id);
  const st = s.settings;
  const canEdit = s.my_permission === 'edit' || s.my_permission === 'owner';
  const owner = s.my_permission === 'owner';
  const mineOut = s.checked_out_by && s.checked_out_by === S.me.id;
  const status = h('div');

  const badges = h('div', {},
    h('span', { class: 'badge' }, s.template.name),
    h('span', { class: 'badge' }, 'your access: ' + s.my_permission),
    s.expired ? h('span', { class: 'badge bad' }, 'password expired') :
      s.expires_at ? h('span', { class: 'badge' }, 'expires ' + when(s.expires_at)) : null,
    s.checked_out_by ? h('span', { class: 'badge warn' }, 'checked out by ' + userName(s.checked_out_by) + ' until ' + when(s.checked_out_until)) : null,
    st.require_approval ? h('span', { class: 'badge info' }, 'approval required') : null);

  // fields + reveal
  const valueEls = {};
  const copyBtns = {};
  const rows = s.fields.map(f => {
    const v = h('span', { class: 'value masked' }, f.has_value ? '••••••••' : '(empty)');
    valueEls[f.slug] = v;
    const copy = h('button', { class: 'btn ghost small', type: 'button', disabled: true,
      onclick: ev => copyValue(ev.currentTarget.getAttribute('data-copy') || '') }, 'Copy');
    copyBtns[f.slug] = copy;
    return h('div', { class: 'field' }, h('span', { class: 'name' }, f.name), v, f.has_value ? copy : h('span'));
  });
  const comment = h('input', { placeholder: st.require_comment ? 'Reason (required)' : 'Reason (optional, recorded in the audit log)' });
  const reveal = async () => {
    try {
      const values = await api('POST', '/api/secrets/' + id + '/reveal', { comment: comment.value.trim() || null });
      for (const [slug, val] of Object.entries(values)) {
        if (!valueEls[slug]) continue;
        valueEls[slug].textContent = val; valueEls[slug].classList.remove('masked');
        valueEls[slug].setAttribute('data-secret-value', '1');
        copyBtns[slug].disabled = false; copyBtns[slug].setAttribute('data-copy', val);
      }
      status.replaceChildren(flash('info', 'Shown for ' + REVEAL_SECONDS + ' seconds. This view is recorded in the audit log.'));
      clearTimeout(revealTimer);
      revealTimer = setTimeout(() => { hideRevealed(); status.replaceChildren(); }, REVEAL_SECONDS * 1000);
    } catch (e) { status.replaceChildren(flash(e.status === 409 ? 'warn' : 'bad', errText(e))); }
  };

  const actions = h('div', { class: 'actions' });
  const needsRequest = !s.access_granted && st.require_approval;
  if (!needsRequest && (!st.require_checkout || mineOut))
    actions.append(h('button', { class: 'btn', type: 'button', onclick: reveal }, 'Reveal'));
  if (st.require_checkout && !mineOut && !needsRequest)
    actions.append(h('button', { class: 'btn', type: 'button', disabled: s.checked_out_by && !owner ? true : null, onclick: async () => {
      try { await api('POST', '/api/secrets/' + id + '/check-out', { comment: comment.value.trim() || null }); toast('Checked out to you.'); render(); }
      catch (e) { status.replaceChildren(flash('bad', errText(e))); }
    } }, 'Check out'));
  if (st.require_checkout && (mineOut || (s.checked_out_by && owner)))
    actions.append(h('button', { class: 'btn secondary', type: 'button', onclick: async () => {
      try {
        const r = await api('POST', '/api/secrets/' + id + '/check-in');
        toast(r && r.rotated ? 'Checked in. The password was changed.' : 'Checked in.'); render();
      } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
    } }, 'Check in'));
  if (canEdit)
    actions.append(h('button', { class: 'btn secondary', type: 'button', onclick: async () => {
      if (!confirm('Generate and store a new password for "' + s.name + '" now?')) return;
      try { await api('POST', '/api/secrets/' + id + '/rotate'); toast('Password changed.'); render(); }
      catch (e) { status.replaceChildren(flash('bad', errText(e))); }
    } }, 'Change password now'));

  let requestBox = null;
  if (needsRequest) {
    const rf = h('form', { onsubmit: async ev => {
      ev.preventDefault();
      try {
        await api('POST', '/api/secrets/' + id + '/access-requests', { reason: fieldVal(rf, 'reason'), minutes: Number(fieldVal(rf, 'minutes')) });
        toast('Request sent to the approvers.'); go('#/requests');
      } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
    } },
      h('p', {}, 'This secret needs an approver\'s OK before you can view it.'),
      h('div', { class: 'row' },
        h('label', {}, 'Why do you need it?', h('input', { name: 'reason', required: true })),
        h('label', {}, 'For how long', h('select', { name: 'minutes' },
          [[30, '30 minutes'], [60, '1 hour'], [240, '4 hours'], [480, '8 hours'], [1440, '24 hours']].map(([m, l]) =>
            h('option', { value: m, selected: m === 60 ? true : null }, l))))),
      h('button', { class: 'btn', type: 'submit' }, 'Request access'));
    requestBox = h('div', { class: 'card' }, h('h2', {}, 'Request access'), rf);
  }

  const tabs = [['details', 'Details']];
  if (canEdit) tabs.push(['edit', 'Edit']);
  if (owner) tabs.push(['history', 'History'], ['permissions', 'Permissions'], ['audit', 'Audit']);
  const tabBar = h('div', { class: 'tabs' }, tabs.map(([k, l]) =>
    h('a', { href: '#/secret/' + id + (k === 'details' ? '' : '/' + k), class: tab === k ? 'active' : null }, l)));

  let body;
  if (tab === 'edit' && canEdit) body = editSecretForm(s);
  else if (tab === 'history' && owner) body = await historyView(id);
  else if (tab === 'permissions' && owner) body = await permissionsEditor('secret', id, s.inherit_permissions);
  else if (tab === 'audit' && owner) body = await secretAudit(id);
  else body = h('div', {},
    st.require_comment || st.require_checkout ? h('label', {}, comment) : h('details', { class: 'small' },
      h('summary', { class: 'muted' }, 'Add a reason (optional)'), comment),
    actions, status, rows,
    h('h2', {}, 'Rules'),
    h('ul', { class: 'small' },
      SETTINGS.filter(([k]) => st[k]).map(([, label]) => h('li', {}, label)),
      st.require_checkout ? h('li', {}, 'Check-out lasts ' + st.checkout_minutes + ' minutes') : null,
      st.expiry_days ? h('li', {}, 'Password expires every ' + st.expiry_days + ' days') : null,
      !SETTINGS.some(([k]) => st[k]) && !st.expiry_days ? h('li', { class: 'muted' }, 'No special rules') : null),
    h('p', { class: 'muted small' }, 'Password last changed ' + (when(s.password_changed_at) || 'never') + ' · updated ' + when(s.updated_at)));

  mount(shell('secrets',
    h('p', { class: 'small' }, h('a', { href: '#/secrets?folder=' + s.folder_id }, '← ' + folderPath(s.folder_id))),
    h('div', { class: 'card' }, h('h1', {}, s.name), badges, tabBar, body),
    requestBox));
}

function settingsInputs(st) {
  return h('div', {},
    SETTINGS.map(([k, label]) => h('label', { class: 'inline' }, h('input', { type: 'checkbox', name: k, checked: st[k] }), label)),
    h('div', { class: 'row' },
      h('label', {}, 'Check-out length (minutes)', h('input', { name: 'checkout_minutes', type: 'number', min: '1', value: st.checkout_minutes || 60 })),
      h('label', {}, 'Approvers (group)', h('select', { name: 'approver_group_id' },
        h('option', { value: '' }, 'Owners of the secret'),
        S.groups.map(g => h('option', { value: g.id, selected: st.approver_group_id === g.id ? true : null }, g.name)))),
      h('label', {}, 'Password expires after (days)', h('input', { name: 'expiry_days', type: 'number', min: '1', value: st.expiry_days || '', placeholder: 'never' }))));
}
function readSettings(form) {
  const out = {};
  for (const [k] of SETTINGS) out[k] = form.elements[k].checked;
  out.checkout_minutes = Number(fieldVal(form, 'checkout_minutes')) || 60;
  const g = fieldVal(form, 'approver_group_id'); if (g) out.approver_group_id = Number(g);
  const e = fieldVal(form, 'expiry_days'); if (e) out.expiry_days = Number(e);
  return out;
}

function fieldInput(f, templateId, required) {
  const isPw = (f.type || '').toLowerCase() === 'password';
  const input = h(f.type === 'notes' || f.type === 'textarea' ? 'textarea' : 'input', {
    name: 'f_' + f.slug, type: isPw ? 'password' : 'text', autocomplete: 'off', required: required && f.required ? true : null,
    placeholder: required ? '' : 'leave empty to keep the current value' });
  if (!isPw) return h('label', {}, f.name || f.slug, input);
  return h('label', {}, f.name || f.slug, h('div', { class: 'toolbar' }, input,
    h('button', { class: 'btn secondary small', type: 'button', onclick: async () => {
      try { input.value = (await api('GET', '/api/password/generate' + (templateId ? '?template_id=' + templateId : ''))).password; input.type = 'text'; }
      catch (e) { toast(errText(e), true); }
    } }, 'Generate'),
    h('button', { class: 'btn ghost small', type: 'button', onclick: () => { input.type = input.type === 'password' ? 'text' : 'password'; } }, 'Show')));
}

function editSecretForm(s) {
  const status = h('div');
  const tpl = S.templates.find(t => t.id === s.template.id) || { fields: s.fields };
  const form = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    const fields = {};
    for (const f of tpl.fields) { const v = form.elements['f_' + f.slug].value; if (v) fields[f.slug] = v; }
    const body = { name: fieldVal(form, 'name'), comment: fieldVal(form, 'comment') || null };
    if (Object.keys(fields).length) body.fields = fields;
    const folder = Number(fieldVal(form, 'folder_id')); if (folder !== s.folder_id) body.folder_id = folder;
    if (s.my_permission === 'owner') body.settings = readSettings(form);
    try { await api('PATCH', '/api/secrets/' + s.id, body); toast('Saved.'); go('#/secret/' + s.id); }
    catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } },
    status,
    h('div', { class: 'row' },
      h('label', {}, 'Name', h('input', { name: 'name', value: s.name, required: true })),
      h('label', {}, 'Folder', h('select', { name: 'folder_id' }, S.folders.filter(f => canEditFolder(f) || f.id === s.folder_id).map(f =>
        h('option', { value: f.id, selected: f.id === s.folder_id ? true : null }, folderPath(f.id)))))),
    h('h2', {}, 'Values'),
    h('p', { class: 'muted small' }, 'Only fill in what you want to change. Old values are kept in the history.'),
    tpl.fields.map(f => fieldInput(f, s.template.id, false)),
    s.my_permission === 'owner' ? [h('h2', {}, 'Rules'), settingsInputs(s.settings)] : null,
    h('label', {}, 'Comment for the audit log', h('input', { name: 'comment' })),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'submit' }, 'Save'),
      s.my_permission === 'owner' ? h('button', { class: 'btn danger', type: 'button', onclick: async () => {
        if (!confirm('Delete "' + s.name + '"? It is removed from search; the audit trail is kept.')) return;
        try { await api('DELETE', '/api/secrets/' + s.id); toast('Deleted.'); go('#/secrets?folder=' + s.folder_id); }
        catch (e) { status.replaceChildren(flash('bad', errText(e))); }
      } }, 'Delete secret') : null));
  return form;
}

async function historyView(id) {
  const wrap = h('div', {}, h('p', { class: 'muted small' },
    'Previous values, newest first. Opening the history is recorded in the audit log.'));
  const load = h('button', { class: 'btn secondary', type: 'button', onclick: async () => {
    try {
      const rows = await api('GET', '/api/secrets/' + id + '/history');
      load.remove();
      wrap.append(rows.length ? h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Field'), h('th', {}, 'Old value'), h('th', {}, 'Replaced'), h('th', {}, 'By'))),
        h('tbody', {}, rows.map(r => {
          const v = h('span', { class: 'value masked', 'data-secret-value': '1' }, '••••••••');
          return h('tr', {}, h('td', {}, r.slug),
            h('td', {}, v, ' ', h('button', { class: 'btn ghost small', type: 'button', onclick: () => {
              v.textContent = r.value; v.classList.remove('masked'); v.setAttribute('data-secret-value', '1');
              clearTimeout(revealTimer); revealTimer = setTimeout(hideRevealed, REVEAL_SECONDS * 1000);
            } }, 'Show')),
            h('td', { class: 'small' }, when(r.replaced_at)), h('td', { class: 'small' }, r.replaced_by || 'system'));
        }))) : h('div', { class: 'empty' }, 'No earlier values.'));
    } catch (e) { wrap.append(flash('bad', errText(e))); }
  } }, 'Load history');
  wrap.append(load);
  return wrap;
}

async function secretAudit(id) {
  const rows = await api('GET', '/api/secrets/' + id + '/audit');
  return rows.length ? h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Who', 'What', 'Details', 'IP'].map(t => h('th', {}, t)))),
    h('tbody', {}, rows.map(r => h('tr', {}, h('td', { class: 'small' }, when(r.ts)), h('td', {}, r.username || ''),
      h('td', {}, r.action), h('td', { class: 'mono' }, r.details || ''), h('td', { class: 'small' }, r.ip || '')))))
    : h('div', { class: 'empty' }, 'Nothing recorded yet.');
}

// ---------------------------------------------------------------- permissions editor

async function permissionsEditor(kind, id, inheritDefault) {
  const base = kind === 'secret' ? '/api/secrets/' : '/api/folders/';
  const current = await api('GET', base + id + '/permissions');
  const status = h('div');
  const body = h('tbody');
  const principals = () => [
    ...S.groups.map(g => ['group', g.id, 'Group: ' + g.name]),
    ...(S.users || []).map(u => ['user', u.id, 'User: ' + (u.display_name ? u.display_name + ' (' + u.username + ')' : u.username)])];
  const addRow = (e) => {
    const who = h('select', {}, principals().map(([t, pid, label]) =>
      h('option', { value: t + ':' + pid, selected: e && e.principal_type === t && e.principal_id === pid ? true : null }, label)));
    if (e && !principals().some(([t, pid]) => t === e.principal_type && pid === e.principal_id))
      who.append(h('option', { value: e.principal_type + ':' + e.principal_id, selected: true },
        e.principal_type === 'user' ? userName(e.principal_id) : groupName(e.principal_id)));
    const level = h('select', {}, LEVELS.map(l => h('option', { value: l, selected: e && e.level === l ? true : null }, l)));
    const tr = h('tr', {}, h('td', {}, who), h('td', {}, level),
      h('td', {}, h('button', { class: 'btn ghost small', type: 'button', onclick: () => tr.remove() }, 'Remove')));
    tr._read = () => { const [t, pid] = who.value.split(':'); return { principal_type: t, principal_id: Number(pid), level: level.value }; };
    body.append(tr);
  };
  current.entries.forEach(addRow);
  const inherit = h('input', { type: 'checkbox', checked: current.inherit_permissions });
  return h('div', {},
    h('p', { class: 'muted small' }, 'view = see and use · edit = change values · owner = also manage permissions, history and deletion.' +
      (S.users ? '' : ' Only groups are listed for you; a vault administrator can also add individual people.')),
    h('label', { class: 'inline' }, inherit, kind === 'secret' ? 'Also apply the folder\'s permissions' : 'Also apply the parent folder\'s permissions'),
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Who'), h('th', {}, 'Access'), h('th'))), body),
    h('div', { class: 'actions' },
      h('button', { class: 'btn secondary', type: 'button', onclick: () => addRow(null) }, '+ Add'),
      h('button', { class: 'btn', type: 'button', onclick: async () => {
        const entries = [...body.children].map(tr => tr._read());
        try {
          await api('PUT', base + id + '/permissions', { inherit_permissions: inherit.checked, entries });
          status.replaceChildren(flash('ok', 'Permissions saved.'));
          if (kind === 'folder') S.folders = await api('GET', '/api/folders');
        } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
      } }, 'Save permissions')),
    status);
}

async function editFolderPermissions(folderId) {
  mount(shell('secrets', h('div', { class: 'card' },
    h('p', { class: 'small' }, h('a', { href: '#/secrets?folder=' + folderId }, '← back')),
    h('h1', {}, 'Permissions: ' + folderPath(folderId)),
    await permissionsEditor('folder', folderId))));
}

// ---------------------------------------------------------------- new secret

async function viewNewSecret(params) {
  const writable = S.folders.filter(canEditFolder);
  if (!writable.length) return mount(shell('secrets', flash('warn', 'You need edit access to a folder to add secrets.')));
  const pre = Number(params.get('folder')) || writable[0].id;
  const status = h('div');
  const fieldsBox = h('div');
  const templateSel = h('select', { name: 'template_id', onchange: () => drawFields() },
    S.templates.map(t => h('option', { value: t.id }, t.name)));
  const drawFields = () => {
    const t = S.templates.find(x => x.id === Number(templateSel.value));
    fieldsBox.replaceChildren(...(t ? t.fields.map(f => fieldInput(f, t.id, true)) : []));
  };
  drawFields();
  const form = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    const t = S.templates.find(x => x.id === Number(templateSel.value));
    const fields = {};
    for (const f of t.fields) { const v = form.elements['f_' + f.slug].value; if (v) fields[f.slug] = v; }
    const pwField = t.fields.find(f => (f.type || '') === 'password');
    try {
      const s = await api('POST', '/api/secrets', {
        name: fieldVal(form, 'name'), folder_id: Number(fieldVal(form, 'folder_id')), template_id: t.id, fields,
        generate_password: !!(pwField && !fields[pwField.slug]), settings: readSettings(form) });
      toast('Secret saved.'); go('#/secret/' + s.id);
    } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } },
    status,
    h('div', { class: 'row' },
      h('label', {}, 'Name', h('input', { name: 'name', required: true, autofocus: true, placeholder: 'e.g. SQL01 sa account' })),
      h('label', {}, 'Folder', h('select', { name: 'folder_id' }, writable.map(f =>
        h('option', { value: f.id, selected: f.id === pre ? true : null }, folderPath(f.id))))),
      h('label', {}, 'Type', templateSel)),
    h('h2', {}, 'Values'),
    h('p', { class: 'muted small' }, 'Leave the password empty to have the vault generate a strong one.'),
    fieldsBox,
    h('details', {}, h('summary', {}, 'Rules (check-out, approval, expiry)'), settingsInputs({ checkout_minutes: 60 })),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'submit' }, 'Save secret'),
      h('a', { class: 'btn secondary', href: '#/secrets?folder=' + pre }, 'Cancel')));
  mount(shell('secrets', h('div', { class: 'card' }, h('h1', {}, 'New secret'), form)));
}

// ---------------------------------------------------------------- access requests

async function viewRequests() {
  const reqs = await api('GET', '/api/access-requests');
  S.pendingReviews = reqs.filter(r => r.status === 'pending' && r.requester !== S.me.username).length;
  const toReview = reqs.filter(r => r.status === 'pending' && r.requester !== S.me.username);
  const mine = reqs.filter(r => r.requester === S.me.username);
  const review = (r, ok) => async () => {
    const note = prompt(ok ? 'Approve — note (optional):' : 'Deny — reason (optional):', '');
    if (note === null) return;
    try { await api('POST', '/api/access-requests/' + r.id + (ok ? '/approve' : '/deny'), { note: note || null }); toast(ok ? 'Approved.' : 'Denied.'); render(); }
    catch (e) { toast(errText(e), true); }
  };
  const statusBadge = r => h('span', { class: 'badge ' + ({ approved: 'ok', denied: 'bad', pending: 'warn' }[r.status] || '') }, r.status);
  mount(shell('requests',
    h('div', { class: 'card' }, h('h1', {}, 'Waiting for your decision'),
      toReview.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Secret', 'Requested by', 'Reason', 'For', 'When', ''].map(t => h('th', {}, t)))),
        h('tbody', {}, toReview.map(r => h('tr', {}, h('td', {}, r.secret_name), h('td', {}, r.requester), h('td', {}, r.reason),
          h('td', {}, r.minutes + ' min'), h('td', { class: 'small' }, when(r.created_at)),
          h('td', {}, h('button', { class: 'btn small', type: 'button', onclick: review(r, true) }, 'Approve'), ' ',
            h('button', { class: 'btn danger small', type: 'button', onclick: review(r, false) }, 'Deny'))))))
        : h('div', { class: 'empty' }, 'Nothing to review.')),
    h('div', { class: 'card' }, h('h1', {}, 'My requests'),
      mine.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Secret', 'Reason', 'Status', 'Note', 'Access until'].map(t => h('th', {}, t)))),
        h('tbody', {}, mine.map(r => h('tr', {}, h('td', {}, h('a', { href: '#/secret/' + r.secret_id }, r.secret_name)), h('td', {}, r.reason),
          h('td', {}, statusBadge(r)), h('td', {}, r.review_note || ''), h('td', { class: 'small' }, when(r.expires_at))))))
        : h('div', { class: 'empty' }, 'You haven\'t requested access to anything.'))));
}

// ---------------------------------------------------------------- my account

async function viewAccount() {
  const me = S.me = await api('GET', '/api/auth/me');
  const status = h('div');
  const mfaBox = h('div');
  if (me.auth_source !== 'local') {
    mfaBox.append(h('p', {}, 'You sign in with ' + me.auth_source + '; two-step verification is handled there.'));
  } else if (me.mfa_enabled) {
    mfaBox.append(h('p', {}, h('span', { class: 'badge ok' }, 'on'), ' Two-step verification is set up. If you lose your phone, ask a vault administrator to reset it.'));
  } else {
    mfaBox.append(h('p', {}, 'Protect your vault account with a code from an authenticator app (Microsoft Authenticator, Google Authenticator, …).'),
      h('button', { class: 'btn', type: 'button', onclick: async ev => {
        ev.currentTarget.remove();
        try {
          const e = await api('POST', '/api/auth/mfa/enroll');
          const f = h('form', { onsubmit: async ev2 => {
            ev2.preventDefault();
            try { await api('POST', '/api/auth/mfa/confirm', { code: fieldVal(f, 'code') }); toast('Two-step verification is on.'); render(); }
            catch (err) { status.replaceChildren(flash('bad', errText(err))); }
          } },
            h('ol', {},
              h('li', {}, 'In your authenticator app choose ', h('strong', {}, 'Add account → Other / Enter a setup key'), '.'),
              h('li', {}, 'Account name: ', h('strong', {}, 'Password Vault (' + me.username + ')'), ', key: ', h('span', { class: 'mono' }, e.secret), ' (time-based).'),
              h('li', {}, 'Enter the 6-digit code it shows:')),
            h('div', { class: 'toolbar' }, h('input', { name: 'code', inputmode: 'numeric', maxlength: '6', required: true, class: 'grow' }),
              h('button', { class: 'btn', type: 'submit' }, 'Turn on')),
            h('details', { class: 'small' }, h('summary', { class: 'muted' }, 'Setup link (for apps that accept one)'), h('p', { class: 'mono' }, e.otpauth_uri)));
          mfaBox.append(f);
        } catch (err) { status.replaceChildren(flash('bad', errText(err))); }
      } }, 'Set up two-step verification'));
  }
  mount(shell('account', h('div', { class: 'card' }, h('h1', {}, 'My account'),
    h('table', {}, h('tbody', {},
      [['User name', me.username], ['Name', me.display_name || ''], ['E-mail', me.email || ''], ['Role', me.role],
       ['Signs in with', me.auth_source === 'local' ? 'vault account' : me.auth_source], ['Last sign-in', when(me.last_login)],
       ['Groups', (me.group_ids || []).map(groupName).join(', ') || '—']].map(([k, v]) => h('tr', {}, h('th', {}, k), h('td', {}, v))))),
    h('h2', {}, 'Two-step verification'), status, mfaBox,
    me.auth_source === 'local' ? h('p', { class: 'muted small' }, 'To change your vault password, ask a vault administrator.') : null)));
}

// ---------------------------------------------------------------- administration

async function viewAdmin(section) {
  const tabs = [['users', 'Users'], ['groups', 'Groups'], ['folders', 'Folders'], ['audit', 'Audit log'], ['sso', 'Single sign-on'], ['maintenance', 'Maintenance']];
  const bar = h('div', { class: 'tabs' }, tabs.map(([k, l]) => h('a', { href: '#/admin/' + k, class: section === k ? 'active' : null }, l)));
  const views = { users: adminUsers, groups: adminGroups, folders: adminFolders, audit: adminAudit, sso: adminSso, maintenance: adminMaintenance };
  const content = await (views[section] || adminUsers)();
  mount(shell('admin', h('div', { class: 'card' }, h('h1', {}, 'Administration'), bar, content)));
}

async function adminUsers() {
  S.users = await api('GET', '/api/users');
  const status = h('div');
  const act = (fn, msg) => async () => { try { await fn(); toast(msg); render(); } catch (e) { status.replaceChildren(flash('bad', errText(e))); } };
  const patch = (u, body) => api('PATCH', '/api/users/' + u.id, body);
  const create = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    try {
      await api('POST', '/api/users', { username: fieldVal(create, 'username'), display_name: fieldVal(create, 'display_name') || null,
        email: fieldVal(create, 'email') || null, password: create.elements.password.value, role: fieldVal(create, 'role') });
      toast('User created.'); render();
    } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } },
    h('div', { class: 'row' },
      h('label', {}, 'User name', h('input', { name: 'username', required: true })),
      h('label', {}, 'Full name', h('input', { name: 'display_name' })),
      h('label', {}, 'E-mail', h('input', { name: 'email', type: 'email' })),
      h('label', {}, 'First password (12+ characters)', h('input', { name: 'password', type: 'password', autocomplete: 'new-password', required: true })),
      h('label', {}, 'Role', h('select', { name: 'role' }, h('option', { value: 'user' }, 'user'), h('option', { value: 'administrator' }, 'administrator')))),
    h('button', { class: 'btn', type: 'submit' }, 'Create user'));
  return h('div', {}, status,
    h('p', { class: 'muted small' }, 'People who sign in with Microsoft (Entra) are added automatically the first time they sign in, if single sign-on is set up.'),
    h('table', {}, h('thead', {}, h('tr', {}, ['User', 'Role', 'Signs in with', 'Status', 'Last sign-in', ''].map(t => h('th', {}, t)))),
      h('tbody', {}, S.users.map(u => h('tr', {},
        h('td', {}, h('strong', {}, u.username), u.display_name ? h('div', { class: 'small muted' }, u.display_name) : null),
        h('td', {}, u.role),
        h('td', {}, u.auth_source === 'local' ? 'vault account' : u.auth_source, u.mfa_enabled ? h('span', { class: 'badge ok' }, '2-step') : null),
        h('td', {}, u.enabled ? h('span', { class: 'badge ok' }, 'active') : h('span', { class: 'badge bad' }, 'disabled'),
          u.locked_until && new Date(u.locked_until + (u.locked_until.endsWith('Z') ? '' : 'Z')) > new Date() ? h('span', { class: 'badge warn' }, 'locked') : null),
        h('td', { class: 'small' }, when(u.last_login)),
        h('td', {}, u.id === S.me.id ? h('span', { class: 'muted small' }, 'you') : h('div', { class: 'actions' },
          h('button', { class: 'btn secondary small', type: 'button', onclick: act(() => patch(u, { enabled: !u.enabled }), u.enabled ? 'Disabled.' : 'Enabled.') }, u.enabled ? 'Disable' : 'Enable'),
          h('button', { class: 'btn secondary small', type: 'button', onclick: act(() => patch(u, { role: u.role === 'administrator' ? 'user' : 'administrator' }), 'Role changed.') },
            u.role === 'administrator' ? 'Make user' : 'Make admin'),
          u.auth_source === 'local' ? h('button', { class: 'btn secondary small', type: 'button', onclick: async () => {
            const pw = prompt('New password for ' + u.username + ' (12+ characters, mixed):');
            if (pw) await act(() => patch(u, { password: pw }), 'Password set; their sessions were signed out.')();
          } }, 'Set password') : null,
          h('button', { class: 'btn secondary small', type: 'button', onclick: act(() => api('POST', '/api/users/' + u.id + '/unlock'), 'Unlocked.') }, 'Unlock'),
          u.mfa_enabled ? h('button', { class: 'btn secondary small', type: 'button', onclick: async () => {
            if (confirm('Reset two-step verification for ' + u.username + '? They set it up again at next sign-in.'))
              await act(() => patch(u, { reset_mfa: true }), '2-step reset.')();
          } }, 'Reset 2-step') : null)))))),
    h('h2', {}, 'Add a vault account'), create);
}

async function adminGroups() {
  S.groups = await api('GET', '/api/groups');
  if (!S.users) S.users = await api('GET', '/api/users');
  const status = h('div');
  const create = h('form', { class: 'toolbar', onsubmit: async ev => {
    ev.preventDefault();
    try { await api('POST', '/api/groups', { name: fieldVal(create, 'name'), description: fieldVal(create, 'description') || null }); toast('Group created.'); render(); }
    catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } }, h('input', { name: 'name', placeholder: 'Group name', required: true, class: 'grow' }),
    h('input', { name: 'description', placeholder: 'Description', class: 'grow' }), h('button', { class: 'btn', type: 'submit' }, 'Create group'));
  return h('div', {}, status, create,
    S.groups.length ? S.groups.map(g => {
      const add = h('select', {}, h('option', { value: '' }, 'Add a member…'),
        S.users.filter(u => !(g.member_ids || []).includes(u.id)).map(u => h('option', { value: u.id }, u.display_name ? u.display_name + ' (' + u.username + ')' : u.username)));
      add.addEventListener('change', async () => {
        if (!add.value) return;
        try { await api('POST', '/api/groups/' + g.id + '/members', { user_id: Number(add.value) }); render(); } catch (e) { toast(errText(e), true); }
      });
      return h('div', { class: 'card' },
        h('h2', {}, g.name, g.source && g.source !== 'local' ? h('span', { class: 'badge info' }, 'from ' + g.source) : null),
        g.description ? h('p', { class: 'muted small' }, g.description) : null,
        h('div', { class: 'actions' }, (g.member_ids || []).length ? g.member_ids.map(id => h('span', { class: 'badge' }, userName(id), ' ',
          h('button', { class: 'btn ghost small', type: 'button', title: 'Remove', onclick: async () => {
            try { await api('DELETE', '/api/groups/' + g.id + '/members/' + id); render(); } catch (e) { toast(errText(e), true); }
          } }, '×'))) : h('span', { class: 'muted small' }, 'No members.')),
        add);
    }) : h('div', { class: 'empty' }, 'No groups yet. Groups make sharing easier: give a group access to a folder, then manage membership here.'));
}

async function adminFolders() {
  S.folders = await api('GET', '/api/folders');
  const sel = Number(route().params.get('folder')) || null;
  const status = h('div');
  const create = h('form', { class: 'toolbar', onsubmit: async ev => {
    ev.preventDefault();
    const parent = fieldVal(create, 'parent');
    try { await api('POST', '/api/folders', { name: fieldVal(create, 'name'), parent_id: parent ? Number(parent) : null }); toast('Folder created.'); render(); }
    catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } }, h('input', { name: 'name', placeholder: 'Folder name', required: true, class: 'grow' }),
    h('select', { name: 'parent', class: 'grow' }, h('option', { value: '' }, 'Top level'), S.folders.map(f => h('option', { value: f.id }, 'inside ' + folderPath(f.id)))),
    h('button', { class: 'btn', type: 'submit' }, 'Create folder'));
  return h('div', {}, status, create,
    h('div', { class: 'split' },
      h('div', {}, S.folders.length ? h('ul', { class: 'tree' }, S.folders.map(f => h('li', {},
        h('a', { href: '#/admin/folders?folder=' + f.id, class: sel === f.id ? 'active' : null }, folderPath(f.id))))) : h('p', { class: 'muted' }, 'No folders yet.')),
      h('div', {}, sel ? [h('h2', {}, 'Who can use ' + folderPath(sel)), await permissionsEditor('folder', sel)]
        : h('p', { class: 'muted' }, 'Choose a folder to set who can use it. Secrets in a folder follow its permissions unless set otherwise.'))));
}

async function adminAudit() {
  const action = route().params.get('action') || '';
  const rows = await api('GET', '/api/audit?limit=300' + (action ? '&action=' + encodeURIComponent(action) : ''));
  const verify = h('span');
  const filter = h('form', { class: 'toolbar', onsubmit: ev => { ev.preventDefault(); const a = fieldVal(filter, 'action'); go('#/admin/audit' + (a ? '?action=' + encodeURIComponent(a) : '')); } },
    h('input', { name: 'action', value: action, placeholder: 'Filter by action, e.g. SECRET_VIEW, LOGIN_FAILED', class: 'grow' }),
    h('button', { class: 'btn secondary', type: 'submit' }, 'Filter'),
    h('button', { class: 'btn', type: 'button', onclick: async () => {
      try { const r = await api('GET', '/api/audit/verify'); verify.replaceChildren(r.valid ? flash('ok', 'Audit log intact: ' + r.records_checked + ' entries verified.')
        : flash('bad', 'Audit log check FAILED at entry ' + r.first_bad_id + ' (' + r.records_checked + ' checked). Someone may have altered it.')); }
      catch (e) { verify.replaceChildren(flash('bad', errText(e))); }
    } }, 'Verify integrity'));
  return h('div', {}, filter, verify,
    h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Who', 'Action', 'Target', 'Details', 'IP'].map(t => h('th', {}, t)))),
      h('tbody', {}, rows.map(r => h('tr', {}, h('td', { class: 'small' }, when(r.ts)), h('td', {}, r.username || ''), h('td', {}, r.action),
        h('td', { class: 'small' }, (r.target_type || '') + (r.target_id ? ' ' + r.target_id : '')),
        h('td', { class: 'mono' }, r.details || ''), h('td', { class: 'small' }, r.ip || ''))))));
}

async function adminSso() {
  const providers = await api('GET', '/api/sso/providers');
  const status = h('div');
  const name = 'entra';
  const redirect = location.origin + '/sso/' + name + '/callback';
  const form = h('form', { onsubmit: async ev => {
    ev.preventDefault();
    const tenant = fieldVal(form, 'tenant');
    try {
      await api('POST', '/api/sso/providers', { name, type: 'oidc', enabled: true, jit_provisioning: true, default_role: 'user',
        config: { issuer: 'https://login.microsoftonline.com/' + tenant + '/v2.0', client_id: fieldVal(form, 'client_id'),
          client_secret: form.elements.client_secret.value, username_claim: 'preferred_username', groups_claim: 'groups' } });
      toast('Microsoft sign-in added.'); render();
    } catch (e) { status.replaceChildren(flash('bad', errText(e))); }
  } },
    h('ol', { class: 'small' },
      h('li', {}, 'Entra admin center → App registrations → New registration, name "Password Vault", single tenant.'),
      h('li', {}, 'Redirect URI (Web): ', h('span', { class: 'mono' }, redirect)),
      h('li', {}, 'Certificates & secrets → New client secret; copy its value.'),
      h('li', {}, 'Enter the IDs below.')),
    h('div', { class: 'row' },
      h('label', {}, 'Directory (tenant) ID', h('input', { name: 'tenant', required: true })),
      h('label', {}, 'Application (client) ID', h('input', { name: 'client_id', required: true })),
      h('label', {}, 'Client secret value', h('input', { name: 'client_secret', type: 'password', autocomplete: 'off', required: true }))),
    h('button', { class: 'btn', type: 'submit' }, 'Add Microsoft sign-in'));
  return h('div', {}, status,
    providers.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Name', 'Type', 'Status', 'Redirect / ACS URL', ''].map(t => h('th', {}, t)))),
      h('tbody', {}, providers.map(p => h('tr', {}, h('td', {}, p.name), h('td', {}, p.type),
        h('td', {}, p.enabled ? h('span', { class: 'badge ok' }, 'enabled') : h('span', { class: 'badge' }, 'disabled')),
        h('td', { class: 'mono' }, p.redirect_uri || p.acs_url || ''),
        h('td', {}, h('button', { class: 'btn secondary small', type: 'button', onclick: async () => {
          try { await api('PATCH', '/api/sso/providers/' + encodeURIComponent(p.name), { enabled: !p.enabled }); render(); } catch (e) { toast(errText(e), true); }
        } }, p.enabled ? 'Disable' : 'Enable'))))))
      : h('p', { class: 'muted' }, 'No single sign-on yet: people sign in with vault accounts.'),
    providers.some(p => p.name === name) ? null : [h('h2', {}, 'Sign in with Microsoft (Entra ID)'), form]);
}

async function adminMaintenance() {
  const out = h('div');
  const expired = await api('GET', '/api/secrets/expired');
  return h('div', {},
    h('h2', {}, 'Passwords past their expiry'),
    expired.length ? h('ul', {}, expired.map(s => h('li', {}, h('a', { href: '#/secret/' + s.id }, s.name), ' — expired ' + when(s.expires_at))))
      : h('p', { class: 'muted' }, 'None.'),
    h('h2', {}, 'Release overdue check-outs'),
    h('p', { class: 'muted small' }, 'Checks in anything whose check-out time has passed (changing passwords where the secret is set to).'),
    h('button', { class: 'btn', type: 'button', onclick: async () => {
      try { const r = await api('POST', '/api/maintenance/run'); out.replaceChildren(flash('ok', 'Released ' + r.released.length + ', errors ' + r.errors.length + ', expired ' + r.expired.length + '.')); }
      catch (e) { out.replaceChildren(flash('bad', errText(e))); }
    } }, 'Run now'), out);
}

// ---------------------------------------------------------------- start

window.addEventListener('hashchange', () => { if (S.token) render(); });

(async function start() {
  const params = new URLSearchParams(location.search);
  const code = params.get('code');
  if (code) {
    history.replaceState(null, '', location.pathname + location.hash);   // never keep the code in the address bar
    try { const res = await api('POST', '/api/auth/sso/exchange', { code }); return await startSession(res.access_token); }
    catch (e) { return renderLogin('Microsoft sign-in did not complete: ' + errText(e)); }
  }
  let saved = null;
  try { saved = sessionStorage.getItem('vault_token'); } catch (_) { /* private mode */ }
  if (saved) {
    try { return await startSession(saved); } catch (_) { storeToken(null); }
  }
  renderLogin();
})();
