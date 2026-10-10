"""Tiny MCP client for checking any Streamable HTTP MCP endpoint from the terminal.

Examples:
  .venv/bin/python scripts/mcp_call.py http://localhost:8001/mcp                      # list tools
  .venv/bin/python scripts/mcp_call.py http://localhost:8001/mcp list_orders '{"on_date": "today"}'
  MCP_API_KEY=urm_... .venv/bin/python scripts/mcp_call.py http://app.urumi.test:8880/mcp

The key is read from the MCP_API_KEY environment variable (never a command-line argument, so it
does not land in shell history or process listings) and sent as "Authorization: Bearer <key>".
"""

import asyncio
import json
import os
import sys

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    tool = sys.argv[2] if len(sys.argv) > 2 else None
    arguments = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    headers = {"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"} if os.getenv("MCP_API_KEY") else {}

    async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            if tool is None:
                result = await client.list_tools()
                for item in result.tools:
                    params = ", ".join(item.input_schema.get("properties", {}))
                    print(f"- {item.name}({params}): {item.description}")
                print(f"{len(result.tools)} tools")
                return 0
            result = await client.call_tool(tool, arguments)
            print("is_error:", bool(result.is_error))
            if result.structured_content is not None:
                print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))
            else:
                for item in result.content:
                    print(getattr(item, "text", item))
            return 1 if result.is_error else 0


def _root_cause(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # print one line instead of a traceback
        cause = _root_cause(exc)
        print(f"request failed: {type(cause).__name__}: {cause}", file=sys.stderr)
        sys.exit(3)
