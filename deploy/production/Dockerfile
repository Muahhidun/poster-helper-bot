FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHON_DOTENV_DISABLED=1 PORT=8080
WORKDIR /app
COPY requirements-furniture-mcp.txt /tmp/requirements-furniture-mcp.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-furniture-mcp.txt \
    && rm /tmp/requirements-furniture-mcp.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY furniture_mcp/__init__.py furniture_mcp/archive.py furniture_mcp/auth.py furniture_mcp/config.py furniture_mcp/server.py furniture_mcp/http_archive.py /app/furniture_mcp/
COPY furniture_reader/__init__.py furniture_reader/config.py furniture_reader/store.py furniture_reader/http_contract.py /app/furniture_reader/
USER 10001:10001
CMD ["uvicorn", "furniture_mcp.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-proxy-headers", "--log-level", "warning"]
