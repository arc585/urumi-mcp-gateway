import { useCallback, useEffect, useState } from 'react';
import { gatewayMcpUrl } from '../api.js';
import { Blank, CopyButton, PageHeading, Panel } from '../components.jsx';
import { ServerRow } from './Servers.jsx';

function Metric({ label, value, caption, icon, tint }) {
  return (
    <div className="metric-card">
      <div className={`metric-icon ${tint}`}>{icon}</div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-caption">{caption}</div>
    </div>
  );
}

export default function Overview({ me, statuses, call, onNavigate }) {
  const [recent, setRecent] = useState([]);
  const loadRecent = useCallback(() => call('/audit?limit=50').then(setRecent).catch(() => setRecent([])), [call]);
  useEffect(() => {
    loadRecent();
  }, [loadRecent, statuses]);

  const connected = statuses.filter((item) => item.status === 'Connected');
  const tools = connected.reduce((sum, item) => sum + item.tools.length, 0);
  const ok = recent.filter((row) => row.succeeded).length;

  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE OVERVIEW"
        title={`Hello, ${me.display_name.split(' ')[0]}`}
        description={me.role === 'admin' ? 'You are an admin: you manage servers and people.' : 'You are a member: you use the team’s tools.'}
        action={
          <button type="button" className="button button-light" onClick={() => onNavigate('chat')}>
            ✳ &nbsp; Open assistant
          </button>
        }
      />
      <div className="metric-grid">
        <Metric label="Connected servers" value={`${connected.length} / ${statuses.length}`} caption="Live health check" icon="◈" tint="lavender" />
        <Metric label="Available tools" value={tools} caption="Across connected servers" icon="⌘" tint="blue" />
        <Metric label={me.role === 'admin' ? 'Recent tool calls' : 'Your recent calls'} value={recent.length} caption="From the audit log" icon="≋" tint="peach" />
        <Metric label="Successful" value={recent.length ? `${Math.round((ok / recent.length) * 100)}%` : '—'} caption="Of those calls" icon="✓" tint="mint" />
      </div>
      <div className="overview-grid">
        <Panel
          title="Upstream health"
          description="Refreshed every 10 seconds."
          aside={
            <button type="button" className="text-button" onClick={() => onNavigate('servers')}>
              Servers <span>→</span>
            </button>
          }
        >
          <div className="server-list">
            {statuses.map((server) => (
              <ServerRow key={server.id} server={server} compact />
            ))}
            {!statuses.length && <Blank text="No servers registered." />}
          </div>
        </Panel>
        <Panel
          title="Recent activity"
          aside={
            <button type="button" className="text-button" onClick={() => onNavigate('audit')}>
              Audit log <span>→</span>
            </button>
          }
        >
          <div className="activity-list">
            {recent.slice(0, 6).map((row) => (
              <div key={row.id} className="activity-row">
                <div className={`activity-icon ${row.succeeded ? 'success' : 'failed'}`}>{row.succeeded ? '✓' : '!'}</div>
                <div className="activity-copy">
                  <strong>
                    {row.upstream_name} <span>/</span> {row.tool_name}
                  </strong>
                  <small>
                    {row.user_email} · {row.duration_ms} ms
                  </small>
                </div>
                <time>{new Date(row.occurred_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
              </div>
            ))}
            {!recent.length && <Blank text="Tool calls will appear here." />}
          </div>
        </Panel>
      </div>
      <div className="learning-note">
        <div className="learning-icon">✦</div>
        <div>
          <strong>Your gateway URL</strong>
          <p>
            Point Claude Code, Cursor, or MCP Inspector at <code>{gatewayMcpUrl()}</code> with your own key. You never
            need the upstream servers’ credentials.
          </p>
        </div>
        <CopyButton value={gatewayMcpUrl()} label="Copy URL" />
      </div>
    </>
  );
}
