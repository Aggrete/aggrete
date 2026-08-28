# Build stage: the -dev variant ships pip and a shell.
FROM cgr.dev/chainguard/python:latest-dev AS build
USER root
WORKDIR /app
COPY pyproject.toml README.md ./
COPY aggrete ./aggrete
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir ".[redis]"
# Pre-create the state dir owned by the runtime nonroot user (uid 65532).
RUN mkdir -p /state

# Runtime stage: distroless, no shell, no package manager, runs as nonroot.
FROM cgr.dev/chainguard/python:latest
LABEL org.opencontainers.image.title="aggrete" \
      org.opencontainers.image.description="An MCP proxy that enforces your code of conduct across connectors." \
      org.opencontainers.image.source="https://github.com/cjohannsen81/aggrete" \
      org.opencontainers.image.url="https://aggrete.com"
COPY --from=build /venv /venv
COPY --from=build --chown=65532:65532 /state /var/lib/aggrete
ENV PATH="/venv/bin:$PATH"
WORKDIR /var/lib/aggrete
VOLUME ["/var/lib/aggrete"]
# Mount /etc/aggrete with proxy.config.yaml and coc.yaml.
ENTRYPOINT ["/venv/bin/aggrete", "--config", "/etc/aggrete/proxy.config.yaml"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
EXPOSE 8080
