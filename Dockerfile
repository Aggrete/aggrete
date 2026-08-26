FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY aggrete ./aggrete
RUN pip install --no-cache-dir --prefix=/install ".[redis]"

FROM python:3.13-slim
LABEL org.opencontainers.image.title="aggrete" \
      org.opencontainers.image.description="An MCP proxy that enforces your code of conduct across connectors." \
      org.opencontainers.image.source="https://github.com/cjohannsen81/aggrete" \
      org.opencontainers.image.url="https://aggrete.com"
COPY --from=build /install /usr/local
RUN useradd -r -u 10001 aggrete && mkdir -p /etc/aggrete /var/lib/aggrete && chown aggrete /var/lib/aggrete
USER aggrete
WORKDIR /var/lib/aggrete
# Mount /etc/aggrete with proxy.config.yaml and coc.yaml.
ENTRYPOINT ["aggrete", "--config", "/etc/aggrete/proxy.config.yaml"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
EXPOSE 8080
