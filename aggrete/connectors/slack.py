"""Slack as an Aggrete connector, one tool set per channel.

    python -m aggrete.connectors.slack --token xoxb-... --channels legal,finance,general

The proxy runs this over stdio and holds the bot token; people never do. Each
allowed channel becomes read tools `search_<channel>` / `read_<channel>` and,
with --allow-write, `post_<channel>` (governed as egress by the proxy):

    domains:
      "slack__*_legal": legal-hold      # an information barrier, in Slack
      "slack__*": slack-general

Every message carries its author's Slack user id and, when resolvable, email,
so the policy counts people in Slack results exactly as it does in HR records.
Search is history + local filter, so a plain bot token is enough (no
`search:read` user token required).
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Annotated

import httpx2 as httpx
from pydantic import Field

from aggrete.connectors.base import Connector

__version__ = "0.1.0"
TARGET_API = "slack-web-api"

API = "https://slack.com/api"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class Slack:
    """Minimal Slack Web API client on a bot token. httpx only."""

    def __init__(self, token: str):
        self.token = token
        self.http = httpx.Client(timeout=30, base_url=API,
                                 headers={"Authorization": f"Bearer {token}"})
        self._email: dict[str, str | None] = {}

    def _call(self, method: str, **params) -> dict:
        r = self.http.get("/" + method, params=params)
        r.raise_for_status()
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"slack {method}: {d.get('error')}")
        return d

    def channels(self) -> list[dict]:
        """Public and private channels the bot is a member of."""
        out, cursor = [], ""
        while True:
            d = self._call("conversations.list", types="public_channel,private_channel",
                           limit=200, exclude_archived="true", **({"cursor": cursor} if cursor else {}))
            out += [{"id": c["id"], "name": c["name"]} for c in d.get("channels", [])]
            cursor = (d.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return out

    def history(self, channel_id: str, limit: int = 50) -> list[dict]:
        return self._call("conversations.history", channel=channel_id, limit=limit).get("messages", [])

    def user_email(self, uid: str | None) -> str | None:
        if not uid:
            return None
        if uid not in self._email:
            try:
                prof = self._call("users.info", user=uid).get("user", {}).get("profile", {})
                self._email[uid] = prof.get("email")
            except RuntimeError:
                self._email[uid] = None
        return self._email[uid]

    def post(self, channel_id: str, text: str) -> dict:
        r = self.http.post("/chat.postMessage", json={"channel": channel_id, "text": text})
        r.raise_for_status()
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"slack chat.postMessage: {d.get('error')}")
        return d

    def message_record(self, channel: str, m: dict) -> dict:
        uid = m.get("user") or m.get("bot_id")
        return {"ts": m.get("ts"), "channel": channel, "text": m.get("text", ""),
                "slack_user_id": uid, "user_email": self.user_email(m.get("user"))}


def build(slack: Slack, channel_names: list[str], writable: bool = False) -> Connector:
    c = Connector("slack")
    live = {ch["name"]: ch["id"] for ch in slack.channels()}
    wanted = [(n, live[n]) for n in channel_names if n in live]

    @c.read("channels", (
        "List the Slack channels exposed here, with the search, read and (when writing is enabled) post tool "
        "name for each one. Call this first when asked about Slack, so you know which channel-scoped tool to use "
        "next. Takes no arguments."))
    def channels_tool() -> str:
        """Directory of the available Slack channels and their per-channel tool names.

        Returns JSON: {slack_channels: [{name, search_tool, read_tool, post_tool?}]}.
        """
        return json.dumps({"slack_channels": [
            dict({"name": n, "search_tool": f"search_{slug(n)}", "read_tool": f"read_{slug(n)}"},
                 **({"post_tool": f"post_{slug(n)}"} if writable else {})) for n, _ in wanted]})

    for name, cid in wanted:
        s = slug(name)

        def make(cid=cid, name=name):
            def search(
                query: Annotated[str, Field(default="", description="Keyword to match in message text (case-insensitive); leave empty to list the most recent messages in the channel.")] = "",
            ) -> str:
                """Recent messages in this channel matching the query, each with author id and email.

                Returns JSON: {channel, messages: [{ts, channel, text, slack_user_id, user_email}]}.
                """
                msgs = slack.history(cid, 100)
                if query.strip():
                    q = query.lower()
                    msgs = [m for m in msgs if q in (m.get("text", "").lower())]
                return json.dumps({"channel": name, "messages": [slack.message_record(name, m) for m in msgs[:30]]})

            def read(
                ts: Annotated[str, Field(default="", description="Message timestamp id (the 'ts' field of a search result, for example '1719000000.001200'); leave empty to read the latest message.")] = "",
            ) -> str:
                """One message by its timestamp (or the latest), with author id and email.

                Returns JSON: {channel, message: {ts, channel, text, slack_user_id, user_email}}.
                """
                msgs = slack.history(cid, 100)
                hit = next((m for m in msgs if m.get("ts") == ts), None) if ts else (msgs[0] if msgs else None)
                return json.dumps({"channel": name, "message": slack.message_record(name, hit) if hit else None})

            return search, read

        search, read = make()
        c.read(f"search_{s}", (
            f"Search recent messages in the Slack channel #{name} by keyword. The search is fenced to #{name}, and each "
            f"hit carries the author's Slack user id and, when resolvable, email. Leave the query empty to list recent messages."))(search)
        c.read(f"read_{s}", (
            f"Read one message from the Slack channel #{name} by its timestamp (ts), or the latest message when ts is omitted. "
            f"Fenced to #{name}; the result carries the author's Slack user id and email when resolvable."))(read)
        if writable:
            def make_post(cid=cid, name=name):
                def post(
                    text: Annotated[str, Field(description="Message text to post to the channel.")],
                ) -> str:
                    """Post one message to this channel. Returns JSON: {channel, posted_ts}."""
                    d = slack.post(cid, text)
                    return json.dumps({"channel": name, "posted_ts": d.get("ts")})
                return post
            c.write(f"post_{s}", (
                f"Post a message to the Slack channel #{name}. The message is written only to #{name}, and the proxy governs "
                f"this call as an egress/write. Provide the message text."))(make_post())
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="Slack bot token (xoxb-...)")
    ap.add_argument("--channels", required=True, help="comma-separated channel names to expose (e.g. legal,finance,general)")
    ap.add_argument("--allow-write", action="store_true", help="expose post tools (governed as writes/egress by the proxy)")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    names = [n.strip() for n in a.channels.split(",") if n.strip()]
    slack = Slack(a.token)
    if a.list:
        live = {ch["name"] for ch in slack.channels()}
        for n in names:
            here = "" if n in live else "   (bot is not in this channel)"
            print(f"  #{n!r:24} -> slack__search_{slug(n)}, slack__read_{slug(n)}"
                  + (f", slack__post_{slug(n)}" if a.allow_write else "") + here)
        return
    build(slack, names, writable=a.allow_write).run()


if __name__ == "__main__":
    main()
