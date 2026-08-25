"""Live self-test: review a real commit diff through production MCP server."""
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "https://ca-harness-dev-lm-mcpserver.bravesky-4430540a.swedencentral.azurecontainerapps.io/mcp"
DIFF_FILE = os.path.join(os.environ["TEMP"], "selfdiff.txt")


async def go() -> None:
    diff = open(DIFF_FILE, encoding="utf8", errors="replace").read()
    print("diff lines:", len(diff.splitlines()))
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("review_validate_change", {"diff": diff})
            first = res.content[0]
            raw = getattr(first, "text", None)
            if raw is None:
                raise TypeError(f"expected text content, got {type(first).__name__}")
            d = json.loads(raw)
            print("findingCount:", d["findingCount"], "| blocking:", d["blocking"])
            for f in d["findings"]:
                if "title" in f:
                    sev = str(f.get("severity", "?")).upper()
                    print(
                        f"  [{sev:7}] {f.get('lens','?'):20} {f.get('path','?')}:{f.get('line','?')}  "
                        + f["title"][:80]
                    )


if __name__ == "__main__":
    asyncio.run(go())
