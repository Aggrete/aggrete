"""On-behalf-of credentials: the credential a specific user should use to reach a
specific upstream, so each person's own access is carried end to end instead of
everyone sharing one master credential.

The proxy still never forwards the caller's own token to an upstream. Instead,
for an upstream marked `per_user: true`, it resolves a per-user credential here
and opens a per-user connection with it. Resolution is pluggable:

    upstreams:
      drive:
        command: python3
        args: [-m, aggrete.connectors.drive, --credentials, /opt/aggrete/sa.json, --root, Northwind]
        per_user: true
        obo:
          # Your vault or token-exchange script. Run per (user, upstream) with
          # AGGRETE_USER and AGGRETE_UPSTREAM in the environment; prints JSON:
          #   {"env": {"GOOGLE_DELEGATED_USER": "sam@corp"}, "headers": {...}}
          command: [/opt/aggrete/obo.sh]
          # ...or a static map instead of a command:
          # users:
          #   sam@corp: {env: {GOOGLE_DELEGATED_USER: sam@corp}}

With no `obo`, a `per_user` upstream defaults to passing the caller's identity as
`AGGRETE_ACTING_USER`, so a connector that supports delegation can act as them.
Deterministic; no model in this path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class Credential:
    env: dict = field(default_factory=dict)      # extra environment for a stdio connector
    headers: dict = field(default_factory=dict)  # extra headers for an HTTP upstream

    def key(self) -> str:
        """A stable fingerprint of the credential, for audit without leaking it."""
        import hashlib
        blob = json.dumps({"env": sorted(self.env), "headers": sorted(self.headers)}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _expand(d: dict) -> dict:
    return {str(k): os.path.expandvars(str(v)) for k, v in (d or {}).items()}


def resolve(spec: dict, user: str, upstream: str) -> Credential | None:
    """Resolve the on-behalf-of credential for (user, upstream), or None when the
    upstream is not per-user (the caller uses the shared connection instead).

    Order: a static `obo.users` entry, then an `obo.command` hook, then the
    default of passing the identity as AGGRETE_ACTING_USER.
    """
    if not spec.get("per_user"):
        return None
    obo = spec.get("obo") or {}

    users = obo.get("users") or {}
    if user in users:
        u = users[user] or {}
        return Credential(env=_expand(u.get("env")), headers=_expand(u.get("headers")))

    cmd = obo.get("command")
    if cmd:
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=int(obo.get("timeout", 10)),
                env={**os.environ, "AGGRETE_USER": user, "AGGRETE_UPSTREAM": upstream})
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"aggrete: obo command for {upstream!r} did not run: {e}", file=sys.stderr)
            raise RuntimeError(f"credential resolution failed for upstream {upstream!r}")
        if out.returncode != 0:
            # The command's stderr may carry a token or a vault error; log it for
            # the operator, never return it to the caller.
            print(f"aggrete: obo command for {upstream!r} exited {out.returncode}: "
                  f"{out.stderr.strip()[:500]}", file=sys.stderr)
            raise RuntimeError(f"credential resolution failed for upstream {upstream!r}")
        try:
            data = json.loads(out.stdout or "{}")
        except ValueError:
            print(f"aggrete: obo command for {upstream!r} returned invalid JSON", file=sys.stderr)
            raise RuntimeError(f"credential resolution failed for upstream {upstream!r}")
        return Credential(env=dict(data.get("env") or {}), headers=dict(data.get("headers") or {}))

    # Default: hand the connector the caller's identity so it can delegate.
    return Credential(env={"AGGRETE_ACTING_USER": user})
