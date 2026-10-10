import { useState } from 'react';
import { Blank, Modal, PageHeading, Panel, StatusBadge, formatAgo } from '../components.jsx';

const symbolFor = (id) => (id.startsWith('woo') ? 'W' : id.startsWith('ship') ? '↗' : '◈');

export function ServerRow({ server, compact = false, isAdmin, onEdit, onToggle, onRemove }) {
  const status = server.status || (server.enabled ? 'Unreachable' : 'Disabled');
  return (
    <div className={`server-row ${compact ? 'compact' : ''}`}>
      <div className="server-symbol">{symbolFor(server.id)}</div>
      <div className="server-main">
        <div className="server-title">
          {server.name}
          <StatusBadge status={status} />
          {!compact && server.latency_ms != null && status === 'Connected' && (
            <span className="subtle-count">{server.latency_ms} ms</span>
          )}
        </div>
        <div className="server-subtitle">
          <code>{server.id}__*</code>
          {isAdmin && server.url ? ` · ${server.url}` : ''}
          {isAdmin && server.has_credential ? ' · 🔒 credential stored (encrypted)' : ''}
          {` · ${server.tools?.length || 0} tools`}
          {server.checked_at ? ` · checked ${formatAgo(server.checked_at)}` : ''}
        </div>
        {!compact && server.error && <div className="server-error">{server.error}</div>}
        {!compact && server.tools?.length > 0 && (
          <div className="tool-chips">
            {server.tools.map((tool) =>
              server.admin_only_tools?.includes(tool) ? (
                <span key={tool} className="admin-only" title="Admins only: hidden from members, refused if called">
                  🔒 {`${server.id}__${tool}`}
                </span>
              ) : (
                <span key={tool}>{`${server.id}__${tool}`}</span>
              ),
            )}
          </div>
        )}
      </div>
      {!compact && isAdmin && (
        <div className="row-actions">
          <button type="button" className="text-button" onClick={() => onEdit(server)}>
            Edit
          </button>
          <button type="button" className="button button-light button-small" onClick={() => onToggle(server)}>
            {server.enabled ? 'Disable' : 'Enable'}
          </button>
          <button
            type="button"
            className="icon-button danger"
            title={`Remove ${server.name}`}
            aria-label={`Remove ${server.name}`}
            onClick={() => onRemove(server)}
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}

function UpstreamForm({ initial, onSubmit, onCancel, busy }) {
  const editing = Boolean(initial);
  const [form, setForm] = useState({
    id: initial?.id || '',
    name: initial?.name || '',
    url: initial?.url || '',
    auth_header: initial?.auth_header || '',
    credential: '',
    removeCredential: false,
  });
  const [adminOnly, setAdminOnly] = useState(() => new Set(initial?.admin_only_tools || []));
  const toggleAdminOnly = (tool) =>
    setAdminOnly((current) => {
      const next = new Set(current);
      if (next.has(tool)) next.delete(tool);
      else next.add(tool);
      return next;
    });
  const set = (field) => (event) =>
    setForm((current) => ({ ...current, [field]: event.target.type === 'checkbox' ? event.target.checked : event.target.value }));

  function submit(event) {
    event.preventDefault();
    const fields = { name: form.name.trim(), url: form.url.trim(), auth_header: form.auth_header.trim() || null };
    // When editing, send only what changed, so the admin audit log records real changes.
    const payload = editing
      ? Object.fromEntries(Object.entries(fields).filter(([key, value]) => value !== (initial[key] ?? null)))
      : { ...fields, id: form.id.trim() };
    // Credentials are write-only: blank keeps the stored value, "remove" clears it.
    if (form.credential) payload.credential = form.credential;
    else if (form.removeCredential) payload.credential = '';
    const before = [...(initial?.admin_only_tools || [])].sort().join(',');
    const after = [...adminOnly].sort();
    if (editing && after.join(',') !== before) payload.admin_only_tools = after;
    onSubmit(payload);
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <label>
        Server ID
        <input
          value={form.id}
          onChange={set('id')}
          placeholder="inventory"
          pattern="[a-z0-9][a-z0-9-]{1,62}"
          required
          disabled={editing}
        />
        <small>Becomes the tool prefix: {form.id || 'id'}__tool_name. Cannot change later.</small>
      </label>
      <label>
        Display name
        <input value={form.name} onChange={set('name')} placeholder="Inventory MCP" required />
      </label>
      <label className="span-2">
        Streamable HTTP URL
        <input value={form.url} onChange={set('url')} type="url" placeholder="https://mcp.example.com/mcp" required />
        <small>External servers must use HTTPS and a public address. Internal cluster services are pre-approved by the chart.</small>
      </label>
      <label>
        Credential header
        <input value={form.auth_header} onChange={set('auth_header')} placeholder="Authorization" />
        <small>Leave blank if the server needs no credential.</small>
      </label>
      <label>
        Credential value
        <input
          value={form.credential}
          onChange={set('credential')}
          type="password"
          autoComplete="new-password"
          placeholder={editing && initial?.has_credential ? 'Stored — leave blank to keep' : 'e.g. Bearer abc123'}
        />
        <small>Encrypted on the server. Never shown again, never sent to members or MCP clients.</small>
      </label>
      {editing && initial?.tools?.length > 0 && (
        <fieldset className="tool-policy span-2">
          <legend>Tool permissions</legend>
          <small>Admins-only tools are hidden from members (MCP clients and the assistant) and refused if called by name.</small>
          {initial.tools.map((tool) => (
            <label key={tool} className="checkbox-row">
              <input type="checkbox" checked={adminOnly.has(tool)} onChange={() => toggleAdminOnly(tool)} />
              <code>{`${initial.id}__${tool}`}</code> — admins only
            </label>
          ))}
        </fieldset>
      )}
      {editing && initial?.has_credential && (
        <label className="checkbox-row span-2">
          <input type="checkbox" checked={form.removeCredential} onChange={set('removeCredential')} />
          Remove the stored credential
        </label>
      )}
      <div className="span-2 form-footer">
        <p>{editing ? 'Changes apply to the next tool listing and call.' : 'The server is checked live after it is added.'}</p>
        <div className="button-row">
          {onCancel && (
            <button type="button" className="button button-light" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button className="button button-primary" disabled={busy}>
            {editing ? 'Save changes' : 'Add server'} <span>→</span>
          </button>
        </div>
      </div>
    </form>
  );
}

export default function Servers({ me, upstreams, statuses, call, refresh, notify, fail }) {
  const isAdmin = me.role === 'admin';
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const byId = Object.fromEntries(statuses.map((item) => [item.id, item]));
  const rows = upstreams.map((upstream) => ({ ...upstream, ...byId[upstream.id], enabled: upstream.enabled }));

  async function run(action, message) {
    setBusy(true);
    try {
      await action();
      notify(message);
      await refresh();
      return true;
    } catch (err) {
      fail(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const add = (payload) =>
    run(() => call('/upstreams', { method: 'POST', body: payload }), `${payload.name} added`);
  const save = (payload) =>
    run(() => call(`/upstreams/${editing.id}`, { method: 'PATCH', body: payload }), `${payload.name} updated`).then(
      (ok) => ok && setEditing(null),
    );
  const toggle = (server) =>
    run(
      () => call(`/upstreams/${server.id}`, { method: 'PATCH', body: { enabled: !server.enabled } }),
      server.enabled ? `${server.name} disabled — its tools are hidden from everyone` : `${server.name} enabled`,
    );
  const remove = (server) =>
    window.confirm(`Remove ${server.name}? Its tools disappear for everyone.`) &&
    run(() => call(`/upstreams/${server.id}`, { method: 'DELETE' }), `${server.name} removed`);

  return (
    <>
      <PageHeading
        eyebrow="GATEWAY CONFIGURATION"
        title="MCP servers"
        description={
          isAdmin
            ? 'Register a server once; every member gets its tools through the single gateway URL.'
            : 'Servers your admin has connected. Their tools are available to you through the gateway.'
        }
        action={
          <button type="button" className="button button-light" onClick={refresh}>
            ↻ &nbsp; Check now
          </button>
        }
      />
      <Panel
        title="Registered upstreams"
        description="Health is checked live every 10 seconds. A server that does not answer is marked Unreachable; the others keep working."
        aside={<span className="subtle-count">{rows.length} servers</span>}
      >
        <div className="server-list">
          {rows.map((server) => (
            <ServerRow
              key={server.id}
              server={server}
              isAdmin={isAdmin}
              onEdit={setEditing}
              onToggle={toggle}
              onRemove={remove}
            />
          ))}
          {!rows.length && <Blank text="No upstream servers are registered." />}
        </div>
      </Panel>
      {isAdmin && (
        <Panel
          className="form-panel"
          title="Add an MCP server"
          description="Use the server's Streamable HTTP endpoint, usually ending in /mcp."
          aside={<span className="form-step">ADMIN</span>}
        >
          <UpstreamForm key={rows.length} onSubmit={add} busy={busy} />
        </Panel>
      )}
      {editing && (
        <Modal title={`Edit ${editing.name}`} onClose={() => setEditing(null)}>
          <UpstreamForm initial={editing} onSubmit={save} onCancel={() => setEditing(null)} busy={busy} />
        </Modal>
      )}
    </>
  );
}
