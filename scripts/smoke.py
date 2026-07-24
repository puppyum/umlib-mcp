"""End-to-end smoke test: spawn the server over stdio as a real MCP client,
list tools, and exercise the network-facing tools that don't need a library
login. Run with: uv run python scripts/smoke.py
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command="uv", args=["run", "umlib-mcp"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("TOOLS:", sorted(t.name for t in tools.tools))

        r = await s.call_tool(
            "proxy_url", {"url": "https://www.jstor.org/stable/24265183"}
        )
        print("PROXY_URL:", r.content[0].text)

        r = await s.call_tool("resolve", {"query": "10.1145/3411764.3445642"})
        out = json.loads(r.content[0].text)
        print(
            "RESOLVE:",
            json.dumps(
                {
                    k: out.get(k)
                    for k in (
                        "status",
                        "title",
                        "year",
                        "venue",
                        "publisher_url",
                        "open_access",
                        "proxied_url",
                        "mgetit_url",
                    )
                },
                indent=2,
            ),
        )

        r = await s.call_tool("auth_status", {"live_check": True})
        print("AUTH_STATUS:", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
