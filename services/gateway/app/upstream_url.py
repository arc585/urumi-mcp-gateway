"""SSRF protection for admin-supplied MCP URLs.

An admin could (by mistake or maliciously) register a URL that points the gateway at something
it should never talk to: cloud metadata (169.254.169.254), the database, the Kubernetes API, or
other internal services. Rules:
- Cluster-internal MCP servers are allowed only by exact hostname from INTERNAL_UPSTREAM_HOSTS
  (set by the Helm chart to our own MCP Services), over plain HTTP inside the cluster.
- Everything else must be HTTPS and must resolve only to public (global) IP addresses.
Known limit: DNS is resolved again when connecting, so a hostile DNS server could still
"rebind" between check and use; an egress NetworkPolicy is the stronger second layer.
"""

import ipaddress
import os
import socket
from urllib.parse import urlsplit


def trusted_internal_hosts() -> set[str]:
    raw = os.getenv("INTERNAL_UPSTREAM_HOSTS", "")
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def validate_upstream_url(value: str) -> str | None:
    """Return a safe error message, or None when the destination passes the checks."""
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return "URL must use HTTP or HTTPS and include a hostname"
    if parts.username or parts.password:
        return "Credentials must be supplied in the credential fields, not embedded in the URL"
    if parts.query or parts.fragment:
        return "Query strings and fragments are not allowed in an MCP endpoint URL"
    host = parts.hostname.lower().rstrip(".")
    if host in trusted_internal_hosts():
        return None
    if parts.scheme != "https":
        return "External MCP endpoints must use HTTPS"
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
            }
        except OSError:
            return "The endpoint hostname could not be resolved"
    if not addresses or any(not address.is_global for address in addresses):
        return "The endpoint resolves to a private, loopback, link-local, or reserved address"
    return None
