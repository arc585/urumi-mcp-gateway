import { useCallback, useEffect, useState } from 'react';
import { gatewayMcpUrl } from '../api.js';
import { Blank, CopyButton, KeyReveal, PageHeading, Panel, StatusBadge, formatAgo } from '../components.jsx';

function Snippet({ title, text }) {
  return (
    <div className="snippet">
      <div className="snippet-heading">
        <strong>{title}</strong>
        <CopyButton value={text} />
      </div>
      <pre>{text}</pre>
    </div>
  );
}

export default function Keys({ call, currentKeyPrefix, notify, fail }) {
  const [keys, setKeys] = useState([]);
  const [revealed, setRevealed] = useState('');
  const load = useCallback(() => call('/me/keys').then(setKeys).catch((err) => fail(err.message)), [call, fail]);
  useEffect(() => {
    load();
  }, [load]);

  async function create(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = new FormData(form).get('name') || 'default';
    try {
      const created = await call('/me/keys', { method: 'POST', body: { name } });
      setRevealed(created.api_key);
      form.reset();
      await load();
    } catch (err) {
      fail(err.message);
    }
  }

  async function revoke(key) {
    const own = key.prefix === currentKeyPrefix;
    const warning = own ? ' This is the key this browser is using; you will be signed out.' : '';
    if (!window.confirm(`Revoke ${key.prefix}… (${key.name})?${warning}`)) return;
    try {
      await call(`/me/keys/${key.id}/revoke`, { method: 'POST' });
      notify('Key revoked');
      await load();
    } catch (err) {
      fail(err.message);
    }
  }

  const url = gatewayMcpUrl();
  const placeholder = 'urm_YOUR_KEY';
  return (
    <>
      <PageHeading
        eyebrow="YOUR ACCESS"
        title="API keys & clients"
        description="Use one key per device or client, so you can revoke one without breaking the others."
      />
      <KeyReveal apiKey={revealed} onClose={() => setRevealed('')} />
      <Panel title="Your keys" aside={<span className="subtle-count">{keys.filter((k) => !k.revoked_at).length} active</span>}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>KEY</th>
                <th>NAME</th>
                <th>CREATED</th>
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
                    {key.prefix === currentKeyPrefix && <span className="subtle-count"> · this browser</span>}
                  </td>
                  <td>{key.name}</td>
                  <td>{new Date(key.created_at).toLocaleDateString()}</td>
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
          {!keys.length && <Blank text="No keys." />}
        </div>
        <form className="inline-form" onSubmit={create}>
          <input name="name" placeholder="Key name, e.g. claude-code or laptop" maxLength={80} />
          <button className="button button-primary">Generate key</button>
        </form>
      </Panel>
      <Panel
        title="Connect an MCP client"
        description="Every enabled server's tools through one URL. Your client only ever needs this URL and your own key — never the upstream servers' credentials."
      >
        <div className="connect-url">
          <span>Gateway URL</span>
          <code>{url}</code>
          <CopyButton value={url} />
        </div>
        <div className="snippet-grid">
          <Snippet
            title="Claude Code"
            text={`claude mcp add --transport http urumi ${url} \\\n  --header "Authorization: Bearer ${placeholder}"`}
          />
          <Snippet
            title="Cursor (.cursor/mcp.json)"
            text={JSON.stringify({ mcpServers: { urumi: { url, headers: { Authorization: `Bearer ${placeholder}` } } } }, null, 2)}
          />
          <Snippet
            title="MCP Inspector"
            text={`npx @modelcontextprotocol/inspector\n# Transport: Streamable HTTP\n# URL: ${url}\n# Header: Authorization = Bearer ${placeholder}`}
          />
        </div>
        <div className="inline-note">
          <span>i</span>
          <p>Tools appear as &lt;server&gt;__&lt;tool&gt;, e.g. woocommerce__list_orders. Every call is recorded under your name in the audit log.</p>
        </div>
      </Panel>
    </>
  );
}
