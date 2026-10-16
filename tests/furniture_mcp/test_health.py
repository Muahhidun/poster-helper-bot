"""Production liveness must not turn on the disabled archive or bypass auth."""
import asyncio

import httpx

from furniture_mcp.server import create_app


def test_disabled_process_has_only_private_liveness_response():
    async def check():
        app = create_app({})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://archive.example.com") as client:
            response = await client.get("/healthz")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get("/mcp")).status_code == 404
            assert (await client.get("/.well-known/oauth-protected-resource/mcp")).status_code == 404
            assert (await client.get("/healthz?token=ignored")).status_code == 400
            assert (await client.post("/healthz")).status_code != 200
            assert (await client.get("/healthz", headers=[("Authorization", "a"),
                                                         ("Authorization", "b")])).status_code == 400
    asyncio.run(check())
