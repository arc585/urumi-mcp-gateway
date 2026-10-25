import { useCallback, useEffect, useRef, useState } from 'react';
import { Blank, KeyReveal, Modal, PageHeading, Panel, StatusBadge, formatAgo } from '../components.jsx';

function KeysModal({ user, call, onClose, onChanged, fail }) {
  const [keys, setKeys] = useState([]);
  const [revealed, setRevealed] = useState('');
  const load = useCallback(() => call(`/users/${user.id}/keys`).then(setKeys).catch((err) => fail(err.message)), [call, user.id, fail]);
  useEffect(() => {
    load();
  }, [load]);

  async function issue(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = new FormData(form).get('name') || 'issued by admin';
    try {
      const created = await call(`/users/${user.id}/keys`, { method: 'POST', body: { name } });
      setRevealed(created.api_key);
      form.reset();
      await load();
      onChanged();
    } catch (err) {
      fail(err.message);
    }
  }

  async function revoke(key) {
    if (!window.confirm(`Revoke key ${key.prefix}… (${key.name})? The next request using it is rejected.`)) return;
    try {
      await call(`/keys/${key.id}/revoke`, { method: 'POST' });
      await load();
      onChanged();
    } catch (err) {
      fail(err.message);
    }
  }

  return (
    <Modal title={`${user.display_name}'s API keys`} onClose={onClose}>
      <KeyReveal apiKey={revealed} owner={user.display_name} onClose={() => setRevealed('')} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>KEY</th>
              <th>NAME</th>
              <th>LAST USED</th>
              <th>STATUS</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id}>
                <td>
                  <code>{key.prefix}…</code>
                </td>
                <td>{key.name}</td>
                <td>{formatAgo(key.last_used_at)}</td>
                <td>
                  <StatusBadge status={key.revoked_at ? 'Revoked' : 'Active'} />
                </td>
                <td>
                  {!key.revoked_at && (
                    <button type="button" className="text-button danger-text" onClick={() => revoke(key)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!keys.length && <Blank text="No keys yet." />}
      </div>
      <form className="inline-form" onSubmit={issue}>
        <input name="name" placeholder="Key name, e.g. claude-code" maxLength={80} />
        <button className="button button-primary">Issue new key</button>
      </form>
    </Modal>
  );
}

export default function Team({ me, call, notify, fail }) {
  const [users, setUsers] = useState([]);
  const [revealed, setRevealed] = useState(null);
  const [keysFor, setKeysFor] = useState(null);
  const revealRef = useRef(null); // the new key is shown next to the invite form, where the admin is looking

  const load = useCallback(() => call('/users').then(setUsers).catch((err) => fail(err.message)), [call, fail]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(action, message) {
    try {
      await action();
      notify(message);
      await load();
    } catch (err) {
      fail(err.message);
    }
  }

  async function invite(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    try {
      const created = await call('/users', { method: 'POST', body: data });
      setRevealed({ key: created.api_key, owner: created.display_name });
      form.reset();
      setTimeout(() => revealRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
      notify(`${created.display_name} invited. Copy their key now.`);
      await load();
    } catch (err) {
      fail(err.message);
    }
  }

  const revokeAll = (user) =>
    window.confirm(`Revoke all of ${user.display_name}'s keys? Their next request is rejected.`) &&
    act(() => call(`/users/${user.id}/revoke-keys`, { method: 'POST' }), `${user.display_name}'s keys revoked`);
  const setActive = (user, active) =>
    act(() => call(`/users/${user.id}`, { method: 'PATCH', body: { active } }), `${user.display_name} ${active ? 'reactivated' : 'deactivated'}`);
  const setRole = (user, role) =>
    act(() => call(`/users/${user.id}`, { method: 'PATCH', body: { role } }), `${user.display_name} is now ${role}`);

  return (
    <>
      <PageHeading
        eyebrow="PEOPLE & PERMISSIONS"
        title="Team access"
        description="Each teammate gets personal API keys. Revoking a key blocks its very next request."
      />
      <Panel
        title="Workspace members"
        description="Admins manage servers and people; members use tools and manage their own keys."
        aside={<span className="subtle-count">{users.length} people</span>}
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>MEMBER</th>
                <th>ROLE</th>
                <th>STATUS</th>
                <th>ACTIVE KEYS</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const self = user.id === me.id;
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="table-person">
                        <div className="avatar avatar-small">{user.display_name.slice(0, 1).toUpperCase()}</div>
                        <div>
                          <strong>{user.display_name}</strong>
                          <span>
                            {user.email}
                            {self ? ' · You' : ''}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className={`role-pill ${user.role}`}>{user.role}</span>
                    </td>
                    <td>
                      <StatusBadge status={user.active ? 'Active' : 'Disabled'} />
                    </td>
                    <td>{user.active_keys}</td>
                    <td>
                      <div className="button-row">
                        <button type="button" className="text-button" onClick={() => setKeysFor(user)}>
                          Keys
                        </button>
                        {!self && user.active_keys > 0 && (
                          <button type="button" className="text-button danger-text" onClick={() => revokeAll(user)}>
                            Revoke all keys
                          </button>
                        )}
                        {!self && (
                          <button
                            type="button"
                            className="text-button"
                            onClick={() => setRole(user, user.role === 'admin' ? 'member' : 'admin')}
                          >
                            Make {user.role === 'admin' ? 'member' : 'admin'}
                          </button>
                        )}
                        {!self && (
                          <button type="button" className="text-button" onClick={() => setActive(user, !user.active)}>
                            {user.active ? 'Deactivate' : 'Reactivate'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!users.length && <Blank text="No team members yet." />}
        </div>
      </Panel>
      <Panel
        className="form-panel"
        title="Invite a teammate"
        description="Creates their account and first API key. Share the key through a private channel."
        aside={<span className="form-step">INVITE</span>}
      >
        <div ref={revealRef}>
          {revealed && <KeyReveal apiKey={revealed.key} owner={revealed.owner} onClose={() => setRevealed(null)} />}
        </div>
        <form className="form-grid" onSubmit={invite}>
          <label>
            Full name
            <input name="display_name" placeholder="User B" required maxLength={120} />
          </label>
          <label>
            Email address
            <input name="email" type="email" placeholder="b@example.test" required />
          </label>
          <label>
            Role
            <select name="role" defaultValue="member">
              <option value="member">Member — use tools</option>
              <option value="admin">Admin — manage the gateway</option>
            </select>
          </label>
          <div className="form-footer">
            <p>The key appears once, right here.</p>
            <button className="button button-primary">
              Invite <span>→</span>
            </button>
          </div>
        </form>
      </Panel>
      {keysFor && (
        <KeysModal user={keysFor} call={call} fail={fail} onClose={() => setKeysFor(null)} onChanged={load} />
      )}
    </>
  );
}
