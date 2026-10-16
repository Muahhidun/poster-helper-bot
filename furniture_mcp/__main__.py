"""Explicit local-only launch; production HTTPS/proxy deployment is a separate step."""
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("furniture_mcp.server:create_app", factory=True, host="127.0.0.1", port=8001,
                access_log=False, log_level="warning", proxy_headers=False)
