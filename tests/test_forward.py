"""Audit forwarding to a SIEM: queueing, error isolation, and config parsing."""

from __future__ import annotations

from aggrete import forward
from aggrete.forward import Forwarder, build_forwarder


def test_forwarder_delivers_rows_in_order():
    got: list[dict] = []
    f = Forwarder(lambda r: got.append(r))
    f.send({"a": 1})
    f.send({"a": 2})
    f.flush()
    assert got == [{"a": 1}, {"a": 2}]


def test_forwarder_swallows_sink_errors():
    def boom(_row):
        raise RuntimeError("collector down")
    f = Forwarder(boom)
    f.send({"x": 1})   # must not raise
    f.flush()


def test_build_forwarder_off_by_default():
    assert build_forwarder(None) is None
    assert build_forwarder({}) is None


def test_build_http_expands_env(monkeypatch):
    captured: dict = {}
    monkeypatch.setenv("HEC", "tok123")
    monkeypatch.setattr(forward, "_http_sink",
                        lambda url, headers: captured.update(url=url, headers=headers) or (lambda r: None))
    f = build_forwarder({"http": {"url": "https://x/$HEC",
                                  "headers": {"Authorization": "Splunk ${HEC}"}}})
    assert f is not None
    assert captured["url"] == "https://x/tok123"
    assert captured["headers"]["Authorization"] == "Splunk tok123"


def test_build_syslog(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(forward, "_syslog_sink",
                        lambda host, port, proto: captured.update(host=host, port=port, proto=proto) or (lambda r: None))
    build_forwarder({"syslog": {"host": "siem.internal", "port": 5514, "proto": "tcp"}})
    assert captured == {"host": "siem.internal", "port": 5514, "proto": "tcp"}


def test_audit_emits_to_forwarder():
    from aggrete.audit import Audit
    got: list[dict] = []
    a = Audit(None, forward=Forwarder(lambda r: got.append(r)))
    a.emit(user="u", tool="t", decision="allow")
    a.forward.flush()
    assert len(got) == 1 and got[0]["tool"] == "t" and "hash" in got[0]
