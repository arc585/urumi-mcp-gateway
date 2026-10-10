import { useCallback, useEffect, useMemo, useState } from 'react';
import { Blank, PageHeading, Panel, StatusBadge, formatTime, useInterval } from '../components.jsx';

function ToolCalls({ me, call, upstreams, fail }) {
  const isAdmin = me.role === 'admin';
  const [rows, setRows] = useState([]);
  const [users, setUsers] = useState([]);
  const [filters, setFilters] = useState({ user_id: '', upstream_id: '', outcome: '' });
  const [live, setLive] = useState(true);

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: '200' });
    if (filters.user_id) params.set('user_id', filters.user_id);
    if (filters.upstream_id) params.set('upstream_id', filters.upstream_id);
    return call(`/audit?${params}`).then(setRows).catch((err) => fail(err.message));
  }, [call, fail, filters.user_id, filters.upstream_id]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (isAdmin) call('/users').then(setUsers).catch(() => setUsers([]));
  }, [call, isAdmin]);
  useInterval(load, 5000, live);

  const visible = useMemo(
    () =>
      rows.filter((row) =>
        filters.outcome === '' ? true : filters.outcome === 'ok' ? row.succeeded : !row.succeeded,
      ),
    [rows, filters.outcome],
  );
  const set = (field) => (event) => setFilters((current) => ({ ...current, [field]: event.target.value }));

  return (
    <Panel
      title="Tool calls"
      description={isAdmin ? 'Every call through the gateway, from MCP clients and the assistant.' : 'Your own tool calls.'}
      aside={
        <label className="checkbox-row">
          <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} />
          Live
        </label>
      }
    >
      <div className="filters">
        {isAdmin && (
          <select value={filters.user_id} onChange={set('user_id')} aria-label="Filter by user">
            <option value="">All users</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.email}
              </option>
            ))}
          </select>
        )}
        <select value={filters.upstream_id} onChange={set('upstream_id')} aria-label="Filter by server">
          <option value="">All servers</option>
          {upstreams.map((upstream) => (
            <option key={upstream.id} value={upstream.id}>
              {upstream.name}
            </option>
          ))}
        </select>
        <select value={filters.outcome} onChange={set('outcome')} aria-label="Filter by outcome">
          <option value="">Any outcome</option>
          <option value="ok">Succeeded</option>
          <option value="failed">Failed</option>
        </select>
        <span className="subtle-count">{visible.length} calls</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>WHEN</th>
              <th>USER</th>
              <th>VIA</th>
              <th>SERVER / TOOL</th>
              <th>DURATION</th>
              <th>RESULT</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id}>
                <td>{formatTime(row.occurred_at)}</td>
                <td>
                  <div className="audit-user">
                    <div className="avatar avatar-small">{row.user_email.slice(0, 1).toUpperCase()}</div>
                    {row.user_email}
                  </div>
                </td>
                <td>
                  <span className="role-pill">{row.channel === 'chat' ? 'assistant' : 'MCP client'}</span>
                </td>
                <td>
                  <div className="audit-tool">
                    <strong>{row.upstream_name}</strong>
                    <code>{row.tool_name}</code>
                  </div>
                </td>
                <td>{row.duration_ms} ms</td>
                <td>
                  <StatusBadge status={row.succeeded ? 'Success' : 'Failed'} />
                  {row.error && <div className="server-error">{row.error}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length && <Blank text="No tool calls match." />}
      </div>
    </Panel>
  );
}

function AdminEvents({ call, fail }) {
  const [events, setEvents] = useState([]);
  const load = useCallback(() => call('/admin-events?limit=200').then(setEvents).catch((err) => fail(err.message)), [call, fail]);
  useEffect(() => {
    load();
  }, [load]);
  useInterval(load, 5000);
  return (
    <Panel title="Admin actions" description="Invites, key changes, and server changes, with who made them.">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>WHEN</th>
              <th>ADMIN</th>
              <th>ACTION</th>
              <th>TARGET</th>
              <th>DETAIL</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td>{formatTime(event.occurred_at)}</td>
                <td>{event.actor_email}</td>
                <td>
                  <code>{event.action}</code>
                </td>
                <td>{event.target}</td>
                <td>{event.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!events.length && <Blank text="No admin actions yet." />}
      </div>
    </Panel>
  );
}

export default function Audit({ me, call, upstreams, fail }) {
  const [tab, setTab] = useState('calls');
  const isAdmin = me.role === 'admin';
  return (
    <>
      <PageHeading
        eyebrow="SECURITY & HISTORY"
        title="Audit log"
        description="Who called which tool on which server, when, how long it took, and whether it worked."
      />
      {isAdmin && (
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'calls'} className={tab === 'calls' ? 'active' : ''} onClick={() => setTab('calls')}>
            Tool calls
          </button>
          <button type="button" role="tab" aria-selected={tab === 'admin'} className={tab === 'admin' ? 'active' : ''} onClick={() => setTab('admin')}>
            Admin actions
          </button>
        </div>
      )}
      {tab === 'calls' || !isAdmin ? (
        <ToolCalls me={me} call={call} upstreams={upstreams} fail={fail} />
      ) : (
        <AdminEvents call={call} fail={fail} />
      )}
    </>
  );
}
