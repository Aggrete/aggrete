# Self-contained image for the Glama inspector and anyone who wants to try the
# proxy against the bundled mock connectors. It starts an MCP server over stdio
# and answers introspection (tools/list) with the mock HR, finance and ops tools.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir aggrete
COPY proxy.config.yaml coc.yaml ./
COPY demo/mock_server.py ./demo/mock_server.py
COPY brand-icon.svg ./
# stdio MCP server (default transport) against the bundled mocks
ENTRYPOINT ["aggrete", "--config", "proxy.config.yaml"]
