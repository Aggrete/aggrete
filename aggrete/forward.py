"""Ship each audit row to a SIEM as it is written, so decisions land in Splunk,
Elastic, Datadog or syslog next to the rest of your security telemetry.

The local hash-chained `audit.jsonl` stays the system of record; this is a copy
on the wire. Forwarding is best-effort and off the hot path: rows go onto a queue
and a daemon thread sends them, so a slow or down collector never blocks or
breaks a tool call. Dependency-free (stdlib urllib / socket).

    audit_forward:
      http: {url: "https://http-inputs.example.splunkcloud.com/services/collector/raw",
             headers: {Authorization: "Splunk ${HEC_TOKEN}"}}
    # or
    audit_forward:
      syslog: {host: siem.internal, port: 514, proto: udp}
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import urllib.request


class Forwarder:
    """A background sender. `sink(row)` does the actual transport; failures are
    swallowed (logged once) so audit forwarding can never take the proxy down."""

    def __init__(self, sink, name: str = "sink", maxsize: int = 2000):
        self.sink = sink
        self.name = name
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._errored = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send(self, row: dict) -> None:
        try:
            self.q.put_nowait(row)
        except queue.Full:
            pass  # drop under backpressure rather than block a tool call

    def flush(self, timeout: float | None = None) -> None:
        """Wait for the queue to drain (used by tests and clean shutdown)."""
        self.q.join()

    def _run(self) -> None:
        while True:
            row = self.q.get()
            try:
                if row is not None:
                    self.sink(row)
            except Exception as e:  # a down collector must not matter
                if not self._errored:
                    print(f"aggrete: audit forward ({self.name}) failed, will keep trying quietly: {e}",
                          file=sys.stderr)
                    self._errored = True
            finally:
                self.q.task_done()
            if row is None:
                return


def _http_sink(url: str, headers: dict):
    def sink(row: dict) -> None:
        data = json.dumps(row, default=str).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    return sink


def _syslog_sink(host: str, port: int, proto: str):
    proto = proto.lower()

    def sink(row: dict) -> None:
        # RFC 3164-ish: <priority>tag: message. 134 = local0.informational.
        msg = b"<134>aggrete: " + json.dumps(row, default=str).encode()
        if proto == "tcp":
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(msg + b"\n")
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.sendto(msg, (host, port))
            finally:
                s.close()
    return sink


def build_forwarder(cfg: dict | None) -> Forwarder | None:
    """Build a forwarder from an `audit_forward:` block, or None when unset.
    URL and header values may reference ${ENV} so tokens stay out of the config."""
    if not cfg:
        return None
    if cfg.get("http"):
        h = cfg["http"]
        url = os.path.expandvars(h["url"])
        headers = {k: os.path.expandvars(str(v)) for k, v in (h.get("headers") or {}).items()}
        return Forwarder(_http_sink(url, headers), "http")
    if cfg.get("syslog"):
        s = cfg["syslog"]
        return Forwarder(_syslog_sink(os.path.expandvars(str(s["host"])),
                                      int(s.get("port", 514)), s.get("proto", "udp")), "syslog")
    return None
