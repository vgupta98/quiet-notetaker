#!/usr/bin/env python3
"""MCP server over the meeting notes. Read-only, four tools, stdio transport.

The transport is JSON-RPC 2.0, one message per line, on stdin and stdout.
stdout carries protocol and nothing else: one stray byte there ends the
session, so every diagnostic goes to stderr.

Run it with no arguments: `python3 mcp/server.py`.
Standard library only. Python 3.13.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable, IO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import index  # noqa: E402  (needs the path above when run as a script)

SERVER_NAME = "quiet-notetaker"
SERVER_VERSION = "0.1.0"

# Newest first. The client's version is echoed back when we speak it.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2024-11-05")
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[0]

METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603
PARSE_ERROR = -32700

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD.", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "meetings_search",
        "description": (
            "Search the user's recorded meetings by keyword. START HERE for any "
            "question about what was said, agreed or promised in a meeting.\n"
            "Returns pointers and short snippets only — never whole documents: "
            "{total, shown, results:[{id, title, date, attendees, matched_in, snippet}]}. "
            "Read a hit with meetings_get, which returns the written notes for one meeting.\n"
            "matched_in is 'notes' when the summary matched, and 'transcript' when only the "
            "raw speech matched. A transcript-only hit means the notes do not cover it.\n"
            "Do not call meetings_transcript to browse: it is expensive and returns hundreds "
            "of raw speech-to-text lines. Use it only when the notes are insufficient."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to look for. Supports AND, OR, NOT and \"quoted phrases\".",
                },
                "from": dict(_DATE, description="Only meetings on or after this date."),
                "to": dict(_DATE, description="Only meetings on or before this date."),
                "with": {
                    "type": "string",
                    "description": "Only meetings with this attendee. Matches part of a name.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many results to return. Default 10.",
                    "minimum": 1,
                    "default": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "meetings_get",
        "description": (
            "Read the written notes for one meeting: summary, decisions, action items and "
            "open questions. Returns {id, title, date, attendees, sharing, sections}, where "
            "sections maps each heading to its text.\n"
            "This is the cheap way to read a meeting, and it answers most questions on its "
            "own. It never returns the transcript. Get the id from meetings_search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Meeting id, e.g. 2026-08-20-1535-sdk-sync.",
                },
                "section": {
                    "type": "string",
                    "description": "One heading to return, e.g. 'Decisions'. Default: all of them.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "meetings_transcript",
        "description": (
            "Return the raw speech-to-text lines of one meeting: "
            "{id, title, lines, truncated}.\n"
            "EXPENSIVE and noisy. A meeting holds hundreds of lines, misheard words and all. "
            "Read meetings_get first, and call this only when the notes do not answer the "
            "question — to check the exact words someone used, or to find what the summary "
            "left out.\n"
            "Pass 'around' with a MM:SS timestamp to get only the lines near that moment, "
            "which is much cheaper than the whole transcript."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Meeting id."},
                "around": {
                    "type": "string",
                    "description": "Timestamp MM:SS. Returns only the lines near it. Minutes may exceed 59.",
                    "pattern": r"^\d+:\d{2}$",
                },
                "window": {
                    "type": "integer",
                    "description": "Seconds either side of 'around'. Default 60.",
                    "minimum": 0,
                    "default": 60,
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "meetings_actions",
        "description": (
            "List action items taken from the meeting notes: "
            "{total, shown, items:[{id, date, meeting_title, whose, owner, text, done}]}.\n"
            "Use it for 'what do I owe', 'what is outstanding', or what someone else promised. "
            "By default it returns the user's own open items, newest meeting first. "
            "whose='theirs' returns what other people agreed to do; owner names them when the "
            "notes said who."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "done", "all"],
                    "description": "Default 'open'.",
                    "default": "open",
                },
                "whose": {
                    "type": "string",
                    "enum": ["mine", "theirs", "all"],
                    "description": "Default 'mine'.",
                    "default": "mine",
                },
                "from": dict(_DATE, description="Only meetings on or after this date."),
                "to": dict(_DATE, description="Only meetings on or before this date."),
                "limit": {
                    "type": "integer",
                    "description": "How many items to return. Default 50.",
                    "minimum": 1,
                    "default": 50,
                },
            },
            "additionalProperties": False,
        },
    },
]


def log(message: str) -> None:
    """Write a diagnostic. stderr only — stdout belongs to the protocol."""
    print(f"{SERVER_NAME}: {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

def _paths() -> tuple[str, str]:
    directory = index.notes_dir()
    return index.index_path(directory), directory


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Run one tool and return its JSON-ready result.

    The index is refreshed first, so a note edited by hand a second ago is
    still answered correctly.
    """
    db_path, directory = _paths()
    index.refresh(db_path, directory)

    if name == "meetings_search":
        return index.search(
            db_path,
            query=arguments["query"],
            from_=arguments.get("from"),
            to=arguments.get("to"),
            with_=arguments.get("with"),
            limit=int(arguments.get("limit", 10)),
        )
    if name == "meetings_get":
        return index.get(db_path, arguments["id"], section=arguments.get("section"))
    if name == "meetings_transcript":
        return index.transcript(
            db_path,
            arguments["id"],
            around=arguments.get("around"),
            window=int(arguments.get("window", 60)),
        )
    if name == "meetings_actions":
        return index.actions(
            db_path,
            status=arguments.get("status", "open"),
            whose=arguments.get("whose", "mine"),
            from_=arguments.get("from"),
            to=arguments.get("to"),
            limit=int(arguments.get("limit", 50)),
        )
    raise ValueError(f"unknown tool {name!r}")


# --------------------------------------------------------------------------
# JSON-RPC
# --------------------------------------------------------------------------

def _result(request_id: Any, payload: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _text_content(payload: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=None)}]


def handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """Agree a protocol version, and say what this server can do."""
    wanted = params.get("protocolVersion")
    version = wanted if wanted in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call. A failing tool is a result, not a protocol error."""
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    try:
        payload = call_tool(name, arguments)
    except Exception as failure:  # a tool must never take the process down
        traceback.print_exc(file=sys.stderr)
        return {
            "content": [{"type": "text", "text": f"{name} failed: {failure}"}],
            "isError": True,
        }
    return {"content": _text_content(payload), "isError": False}


_METHODS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "initialize": handle_initialize,
    "ping": lambda params: {},
    "tools/list": lambda params: {"tools": TOOLS},
    "tools/call": handle_tools_call,
}


def handle(message: Any) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. Returns None when none is owed."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "expected a JSON-RPC object")

    request_id = message.get("id")
    is_request = "id" in message
    method = message.get("method")
    params = message.get("params") or {}

    if not isinstance(method, str):
        return _error(request_id, INVALID_REQUEST, "missing method") if is_request else None

    if method.startswith("notifications/"):
        return None  # notifications are never answered

    handler = _METHODS.get(method)
    if handler is None:
        log(f"unknown method {method}")
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method {method}") if is_request else None

    try:
        payload = handler(params if isinstance(params, dict) else {})
    except Exception as failure:
        traceback.print_exc(file=sys.stderr)
        return _error(request_id, INTERNAL_ERROR, str(failure)) if is_request else None

    return _result(request_id, payload) if is_request else None


def serve(stream_in: IO[str], stream_out: IO[str]) -> None:
    """Read messages until the client hangs up."""
    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as failure:
            _send(stream_out, _error(None, PARSE_ERROR, f"invalid JSON: {failure}"))
            continue
        response = handle(message)
        if response is not None:
            _send(stream_out, response)


def _send(stream_out: IO[str], response: dict[str, Any]) -> None:
    stream_out.write(json.dumps(response, ensure_ascii=False) + "\n")
    stream_out.flush()


def main() -> int:
    db_path, directory = _paths()
    try:
        stats = index.refresh(db_path, directory)
        log(f"indexed {stats['total']} meetings from {directory}")
    except Exception:
        # A bad index must not stop the handshake; each tool call retries.
        traceback.print_exc(file=sys.stderr)

    try:
        serve(sys.stdin, sys.stdout)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
