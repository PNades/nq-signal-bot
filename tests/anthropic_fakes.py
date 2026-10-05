"""A fake Anthropic client for the gameplan parser tests: records requests, returns canned responses."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "gameplans"


def canned(name: str) -> dict[str, Any]:
    """The canned API answer for a fixture gameplan (`<stem>.response.json`)."""
    return json.loads((FIXTURES / f"{Path(name).stem}.response.json").read_text(encoding="utf-8"))


def message(payload: dict | str | None = None, *, stop_reason: str = "end_turn", model: str = "claude-opus-5-5",
            input_tokens: int = 3000, output_tokens: int = 2000, request_id: str = "req_fake", stop_details=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    m = SimpleNamespace(content=content if payload is not None else [], stop_reason=stop_reason, model=model,
                        stop_details=stop_details,
                        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                              cache_creation_input_tokens=0, cache_read_input_tokens=0))
    m._request_id = request_id
    return m


class _Messages:
    def __init__(self, owner: FakeAnthropic):
        self.owner = owner

    def count_tokens(self, **kw):
        self.owner.counts.append(kw)
        if isinstance(self.owner.count_error, Exception):
            raise self.owner.count_error
        return SimpleNamespace(input_tokens=self.owner.count_tokens)

    def create(self, **kw):
        self.owner.requests.append(kw)
        name = _file_name(kw)
        reply = self.owner.replies.get(name)
        if reply is None:
            reply = self.owner.queue.pop(0) if self.owner.queue else message(canned(name))
        if isinstance(reply, Exception):
            raise reply
        return reply(kw) if callable(reply) else reply


def _file_name(kw: dict) -> str:
    last = kw["messages"][-1]["content"][-1]["text"]
    return last.split("\n", 1)[0].removeprefix("File name: ")


class FakeAnthropic:
    """`replies` maps a file name to a canned message (or an exception to raise); otherwise the queue, else the
    fixture's canned JSON."""

    def __init__(self, replies: dict[str, Any] | None = None, queue: list | None = None, count_tokens: int = 3000,
                 count_error: Exception | None = None):
        self.replies = replies or {}
        self.queue = queue or []
        self.count_tokens = count_tokens
        self.requests: list[dict] = []
        self.counts: list[dict] = []
        self.count_error = count_error
        self.options: list[dict] = []
        self.messages = _Messages(self)

    def with_options(self, **kw):
        self.options.append(kw)
        return self
