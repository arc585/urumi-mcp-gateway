import { useCallback, useEffect, useState } from 'react';
import { ApiError, apiRequest, loadKey, storeKey } from './api.js';
import { useInterval } from './components.jsx';
import Assistant from './pages/Assistant.jsx';
import Audit from './pages/Audit.jsx';
import Keys from './pages/Keys.jsx';
import Overview from './pages/Overview.jsx';
import Servers from './pages/Servers.jsx';
import Team from './pages/Team.jsx';

// `admin: true` pages are hidden from members. This is only tidiness: the gateway API itself
// rejects members on every admin endpoint with 403.
const NAV = [
  { id: 'overview', label: 'Overview', icon: '◫' },
  { id: 'servers', label: 'MCP servers', icon: '◈' },
  { id: 'team', label: 'Team access', icon: '♙', admin: true },
  { id: 'keys', label: 'Keys & clients', icon: '⚿' },
  { id: 'audit', label: 'Audit log', icon: '≋' },
  { id: 'chat', label: 'AI assistant', icon: '✳' },
];

function SignIn({ onSignIn, message }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const key = value.trim();
      const me = await apiRequest(key, '/me'); // verify before keeping it
      onSignIn(key, me);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'That key is not valid or has been revoked.' : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <div className="brand">
        <div className="brand-mark">u</div>
        <div>
          <strong>urumi</strong>
          <span>TEAM GATEWAY</span>
        </div>
      </div>
      <h1>Sign in with your API key</h1>
      <p>Your admin gives you a key when they invite you. Admins: the first key is in the Kubernetes Secret (see the runbook).</p>
      {message && <div className="alert">{message}</div>}
      <form className="key-gate" onSubmit={submit}>
        {error && <div className="alert alert-error">{error}</div>}
        <label htmlFor="gateway-key">API key</label>
        <div className="input-with-action">
          <input
            id="gateway-key"
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="urm_…"
            autoComplete="off"
            required
          />
          <button className="button button-primary" disabled={busy}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
        </div>
        <small>Kept only in this browser tab (sessionStorage) and cleared when you close it.</small>
      </form>
    </div>
  );
}

export default function App() {
  const [key, setKey] = useState(loadKey);
  const [me, setMe] = useState(null);
  const [signInMessage, setSignInMessage] = useState('');
  const [page, setPage] = useState('overview');
  const [upstreams, setUpstreams] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const signOut = useCallback((message = '') => {
    setError('');
    storeKey('');
    setKey('');
    setMe(null);
    setUpstreams([]);
    setStatuses([]);
    setPage('overview');
    setSignInMessage(message);
  }, []);

  // Every API call goes through here; a 401 at any point (key revoked, user deactivated) ends the session.
  const call = useCallback(
    async (path, options) => {
      try {
        return await apiRequest(key, path, options);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          signOut('Your session ended: this key was revoked or the account was deactivated.');
        }
        throw err;
      }
    },
    [key, signOut],
  );

  const notify = useCallback((message) => setNotice(message), []);
  const fail = useCallback((message) => setError(message), []);

  const refresh = useCallback(async () => {
    if (!key) return;
    try {
      const [list, status] = await Promise.all([call('/upstreams'), call('/upstreams/status')]);
      setUpstreams(list);
      setStatuses(status);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) setError(err.message);
    }
  }, [call, key]);

  useEffect(() => {
    if (!key) return;
    call('/me')
      .then(setMe)
      .catch((err) => {
        if (!(err instanceof ApiError && err.status === 401)) signOut(`Could not reach the gateway: ${err.message}`);
      });
  }, [call, key, signOut]);
  useEffect(() => {
    if (me) refresh();
  }, [me, refresh]);
  useInterval(refresh, 10000, Boolean(me) && ['overview', 'servers'].includes(page));
  useEffect(() => setError(''), [page]); // an error belongs to the page where it happened
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(''), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  function signIn(newKey, profile) {
    storeKey(newKey);
    setKey(newKey);
    setMe(profile);
    setSignInMessage('');
  }

  if (!key || !me) {
    return (
      <div className="signin-shell">
        {key && !me ? <p className="thinking">Connecting…</p> : <SignIn onSignIn={signIn} message={signInMessage} />}
      </div>
    );
  }

  const isAdmin = me.role === 'admin';
  const nav = NAV.filter((item) => !item.admin || isAdmin);
  const current = nav.some((item) => item.id === page) ? page : 'overview';
  const shared = { me, call, notify, fail };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">u</div>
          <div>
            <strong>urumi</strong>
            <span>TEAM GATEWAY</span>
          </div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav>
          {nav.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`nav-item ${current === item.id ? 'active' : ''}`}
              onClick={() => setPage(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              {item.id === 'servers' && <span className="nav-count">{upstreams.length || ''}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection-pill">
            <i className="dot dot-green" />
            Signed in as {me.role}
          </div>
          <div className="user-chip">
            <div className="avatar">{me.display_name.slice(0, 1).toUpperCase()}</div>
            <div className="user-details">
              <strong>{me.display_name}</strong>
              <span>{me.email}</span>
            </div>
            <button type="button" className="icon-button" title="Sign out" aria-label="Sign out" onClick={() => signOut()}>
              ⎋
            </button>
          </div>
        </div>
      </aside>
      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span> <strong>{nav.find((item) => item.id === current)?.label}</strong>
          </div>
          <div className="topbar-actions">
            <span className="env-tag">
              <i /> {window.location.host}
            </span>
            <button type="button" className="icon-button" title="Refresh" aria-label="Refresh" onClick={refresh}>
              ↻
            </button>
          </div>
        </header>
        <section className="page-content">
          {error && (
            <div className="alert alert-error" role="alert">
              {error}
              <button type="button" aria-label="Dismiss" onClick={() => setError('')}>
                ×
              </button>
            </div>
          )}
          {notice && <div className="toast">{notice}</div>}
          {current === 'overview' && <Overview {...shared} statuses={statuses} onNavigate={setPage} />}
          {current === 'servers' && <Servers {...shared} upstreams={upstreams} statuses={statuses} refresh={refresh} />}
          {current === 'team' && isAdmin && <Team {...shared} />}
          {current === 'keys' && <Keys {...shared} currentKeyPrefix={key.slice(0, 8)} />}
          {current === 'audit' && <Audit {...shared} upstreams={upstreams} />}
          {current === 'chat' && <Assistant {...shared} />}
        </section>
      </main>
    </div>
  );
}
