#!/usr/bin/env python3
"""Tests for the MCP server, driven as a subprocess over real stdio pipes.

The server is started the way a client starts it: `python3 mcp/server.py`, with
QN_NOTES_DIR pointing at a temporary corpus. Nothing here touches the real
~/Meetings, and nothing imports the server module, so the transport itself is
under test.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import unittest

SERVER = os.path.join(ROOT, "mcp", "server.py")
TIMEOUT_SECONDS = 60
FENCE = "```"

SDK_SYNC = f"""---
title: "SDK Sync"
date: 2026-08-20 15:35
attendees: ["Priya", "Arjun"]
sharing: full
capture: ok
warnings: []
---

# SDK Sync

## Summary
- We settled the retry budget for the mobile sdk.

## Decisions
- Ship the batching change next week.

## My action items
- [ ] Write the migration guide

## Their action items
- [x] Priya: publish the snapshot

## Open questions
- Does the flush interval need a cap?

---

## Transcript

{FENCE}
[00:05] Me: the kestrel dashboard fell over again
[01:37] Them: I will look at the retry budget
{FENCE}
"""

HELD_NOTE = f"""---
title: "Held Chat"
date: 2026-08-21 09:00
attendees: ["Priya"]
sharing: local
capture: ok
warnings: []
---

# Held Chat

## My action items
- [ ] hushword the salary review

---

## Transcript

{FENCE}
[00:02] Me: hushword, this one stays on my mac
{FENCE}
"""

HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def tool_call(request_id: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class ServerTestCase(unittest.TestCase):
    """Gives every test a notes directory and a way to run one session."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.notes = self._temporary.name
        for meeting_id, text in (
            ("2026-08-20-1535-sdk-sync", SDK_SYNC),
            ("2026-08-21-0900-held-chat", HELD_NOTE),
        ):
            with open(os.path.join(self.notes, f"{meeting_id}.md"), "w", encoding="utf-8") as handle:
                handle.write(text)
        self.addCleanup(self._temporary.cleanup)

    def session(self, messages: list[dict]) -> tuple[list[dict], str, int]:
        """Send messages, close stdin, and return (responses, stderr, exit code).

        Every line the server printed on stdout must be JSON: that is asserted
        here, so no test can miss a stray print.
        """
        environment = dict(os.environ, QN_NOTES_DIR=self.notes)
        payload = "".join(json.dumps(message) + "\n" for message in messages)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            process = subprocess.Popen(
                [sys.executable, SERVER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                text=True, env=environment,
            )
            try:
                out, _ = process.communicate(payload, timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                self.fail("the server did not finish; it may be waiting on stdin")
            errors.seek(0)
            stderr = errors.read()

        responses = []
        for number, line in enumerate(out.split("\n"), start=1):
            if not line.strip():
                continue
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                self.fail(f"stdout line {number} is not JSON-RPC: {line!r}\nstderr:\n{stderr}")
        return responses, stderr, process.returncode

    def by_id(self, responses: list[dict]) -> dict:
        return {response["id"]: response for response in responses if "id" in response}


class HandshakeTests(ServerTestCase):
    def test_initialize_returns_a_valid_result(self) -> None:
        responses, _, code = self.session(HANDSHAKE)
        self.assertEqual(code, 0)
        self.assertEqual(len(responses), 1, "the initialized notification must not be answered")
        result = responses[0]["result"]
        self.assertEqual(responses[0]["jsonrpc"], "2.0")
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertIn("name", result["serverInfo"])
        self.assertIn("version", result["serverInfo"])

    def test_an_older_supported_version_is_echoed_back(self) -> None:
        request = json.loads(json.dumps(HANDSHAKE[0]))
        request["params"]["protocolVersion"] = "2024-11-05"
        responses, _, _ = self.session([request])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2024-11-05")

    def test_an_unknown_version_gets_ours(self) -> None:
        request = json.loads(json.dumps(HANDSHAKE[0]))
        request["params"]["protocolVersion"] = "1999-01-01"
        responses, _, _ = self.session([request])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")

    def test_ping_is_answered(self) -> None:
        responses, _, _ = self.session(HANDSHAKE + [{"jsonrpc": "2.0", "id": 9, "method": "ping"}])
        self.assertEqual(self.by_id(responses)[9]["result"], {})


class ToolListTests(ServerTestCase):
    def setUp(self) -> None:
        super().setUp()
        responses, _, _ = self.session(HANDSHAKE + [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        self.tools = self.by_id(responses)[2]["result"]["tools"]

    def test_exactly_four_tools(self) -> None:
        self.assertEqual(
            [tool["name"] for tool in self.tools],
            ["meetings_search", "meetings_get", "meetings_transcript", "meetings_actions"],
        )

    def test_every_tool_has_a_valid_schema(self) -> None:
        for tool in self.tools:
            with self.subTest(tool=tool["name"]):
                self.assertGreater(len(tool["description"]), 40)
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertIsInstance(schema["properties"], dict)
                for name, field in schema["properties"].items():
                    self.assertIn(field["type"], ("string", "integer", "number", "boolean", "array"), name)
                    self.assertIn("description", field, name)
                for required in schema.get("required", []):
                    self.assertIn(required, schema["properties"])

    def test_search_says_it_returns_pointers_and_that_transcripts_cost(self) -> None:
        description = next(tool for tool in self.tools if tool["name"] == "meetings_search")["description"]
        self.assertIn("snippet", description.lower())
        self.assertIn("never whole documents", description.lower())
        self.assertIn("meetings_transcript", description)
        self.assertIn("expensive", description.lower())


class ToolCallTests(ServerTestCase):
    def test_every_tool_returns_parseable_json(self) -> None:
        calls = [
            tool_call(10, "meetings_search", {"query": "retry budget"}),
            tool_call(11, "meetings_get", {"id": "2026-08-20-1535-sdk-sync"}),
            tool_call(12, "meetings_transcript", {"id": "2026-08-20-1535-sdk-sync"}),
            tool_call(13, "meetings_actions", {"status": "all", "whose": "all"}),
        ]
        responses, stderr, code = self.session(HANDSHAKE + calls)
        self.assertEqual(code, 0, stderr)
        answers = self.by_id(responses)

        for request_id in (10, 11, 12, 13):
            result = answers[request_id]["result"]
            self.assertFalse(result.get("isError"), result)
            self.assertEqual(result["content"][0]["type"], "text")
            json.loads(result["content"][0]["text"])  # must parse

        search = json.loads(answers[10]["result"]["content"][0]["text"])
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["results"][0]["id"], "2026-08-20-1535-sdk-sync")
        self.assertEqual(search["results"][0]["matched_in"], "notes")

        meeting = json.loads(answers[11]["result"]["content"][0]["text"])
        self.assertEqual(meeting["title"], "SDK Sync")
        self.assertIn("Decisions", meeting["sections"])

        transcript = json.loads(answers[12]["result"]["content"][0]["text"])
        self.assertEqual(len(transcript["lines"]), 2)

        found = json.loads(answers[13]["result"]["content"][0]["text"])
        self.assertEqual({item["whose"] for item in found["items"]}, {"mine", "theirs"})

    def test_arguments_named_from_to_and_with_reach_the_index(self) -> None:
        calls = [
            tool_call(20, "meetings_search", {"query": "retry", "from": "2026-01-01", "with": "Priya"}),
            tool_call(21, "meetings_search", {"query": "retry", "to": "2026-01-01"}),
            tool_call(22, "meetings_transcript", {"id": "2026-08-20-1535-sdk-sync", "around": "00:05", "window": 10}),
        ]
        responses, stderr, _ = self.session(HANDSHAKE + calls)
        answers = self.by_id(responses)
        self.assertEqual(json.loads(answers[20]["result"]["content"][0]["text"])["total"], 1, stderr)
        self.assertEqual(json.loads(answers[21]["result"]["content"][0]["text"])["total"], 0)
        near = json.loads(answers[22]["result"]["content"][0]["text"])
        self.assertEqual(near["lines"], ["[00:05] Me: the kestrel dashboard fell over again"])
        self.assertTrue(near["truncated"])

    def test_a_local_note_is_out_of_reach(self) -> None:
        calls = [
            tool_call(30, "meetings_search", {"query": "hushword"}),
            tool_call(31, "meetings_get", {"id": "2026-08-21-0900-held-chat"}),
            tool_call(32, "meetings_actions", {"status": "all", "whose": "all"}),
        ]
        responses, _, _ = self.session(HANDSHAKE + calls)
        answers = self.by_id(responses)
        self.assertEqual(json.loads(answers[30]["result"]["content"][0]["text"])["total"], 0)
        self.assertTrue(answers[31]["result"]["isError"])
        found = json.loads(answers[32]["result"]["content"][0]["text"])
        self.assertNotIn("2026-08-21-0900-held-chat", [item["id"] for item in found["items"]])

    def test_a_note_edited_after_startup_is_still_current(self) -> None:
        with open(os.path.join(self.notes, "2026-08-22-1100-late-note.md"), "w", encoding="utf-8") as handle:
            handle.write(SDK_SYNC.replace("SDK Sync", "Late Note").replace("retry budget", "porcupine budget"))
        responses, stderr, _ = self.session(HANDSHAKE + [tool_call(40, "meetings_search", {"query": "porcupine"})])
        found = json.loads(self.by_id(responses)[40]["result"]["content"][0]["text"])
        self.assertEqual(found["total"], 1, stderr)


class FailureTests(ServerTestCase):
    def test_an_unknown_tool_is_an_error_and_the_server_lives_on(self) -> None:
        calls = [
            tool_call(50, "meetings_delete_everything", {}),
            tool_call(51, "meetings_search", {"query": "retry budget"}),
        ]
        responses, stderr, code = self.session(HANDSHAKE + calls)
        answers = self.by_id(responses)
        self.assertTrue(answers[50]["result"]["isError"])
        self.assertIn("unknown tool", answers[50]["result"]["content"][0]["text"])
        self.assertFalse(answers[51]["result"].get("isError"), "the server stopped serving after an error")
        self.assertEqual(code, 0, stderr)

    def test_a_throwing_tool_returns_is_error_and_the_server_lives_on(self) -> None:
        calls = [
            tool_call(60, "meetings_get", {"id": "2026-01-01-0000-no-such-meeting"}),
            tool_call(61, "meetings_search", {}),
            tool_call(62, "meetings_actions", {"status": "perhaps"}),
            tool_call(63, "meetings_search", {"query": "retry budget"}),
        ]
        responses, stderr, code = self.session(HANDSHAKE + calls)
        answers = self.by_id(responses)
        for request_id in (60, 61, 62):
            self.assertTrue(answers[request_id]["result"]["isError"], request_id)
            self.assertEqual(answers[request_id]["result"]["content"][0]["type"], "text")
        self.assertFalse(answers[63]["result"].get("isError"))
        self.assertEqual(code, 0, stderr)

    def test_an_unknown_method_is_a_jsonrpc_error(self) -> None:
        responses, _, code = self.session(
            HANDSHAKE
            + [{"jsonrpc": "2.0", "id": 70, "method": "resources/list"},
               {"jsonrpc": "2.0", "id": 71, "method": "tools/list"}]
        )
        answers = self.by_id(responses)
        self.assertEqual(answers[70]["error"]["code"], -32601)
        self.assertNotIn("result", answers[70])
        self.assertIn("result", answers[71])
        self.assertEqual(code, 0)

    def test_an_unknown_notification_is_never_answered(self) -> None:
        responses, _, _ = self.session(
            HANDSHAKE
            + [{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
               {"jsonrpc": "2.0", "id": 80, "method": "tools/list"}]
        )
        self.assertEqual([response["id"] for response in responses], [1, 80])

    def test_broken_json_does_not_kill_the_session(self) -> None:
        environment = dict(os.environ, QN_NOTES_DIR=self.notes)
        payload = json.dumps(HANDSHAKE[0]) + "\nnot json at all\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 90, "method": "tools/list"}) + "\n"
        process = subprocess.Popen(
            [sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=environment,
        )
        out, _ = process.communicate(payload, timeout=TIMEOUT_SECONDS)
        responses = [json.loads(line) for line in out.split("\n") if line.strip()]
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertIn("result", responses[2])


class StdoutTests(ServerTestCase):
    def test_stdout_carries_json_rpc_only(self) -> None:
        """Diagnostics belong on stderr. One stray byte on stdout ends a session."""
        calls = [
            tool_call(100, "meetings_search", {"query": "retry budget"}),
            tool_call(101, "meetings_get", {"id": "nope"}),
            tool_call(102, "meetings_transcript", {"id": "2026-08-20-1535-sdk-sync"}),
        ]
        # session() already fails on any stdout line that is not JSON.
        responses, stderr, code = self.session(HANDSHAKE + calls)
        self.assertEqual(code, 0)
        self.assertEqual([response["id"] for response in responses], [1, 100, 101, 102])
        for response in responses:
            self.assertEqual(response["jsonrpc"], "2.0")
            self.assertTrue(("result" in response) != ("error" in response))
        self.assertIn("indexed 1 meetings", stderr, "the startup line must go to stderr")
        self.assertIn("Traceback", stderr, "a failing tool must log to stderr")


class StartupTests(ServerTestCase):
    def test_a_missing_notes_directory_does_not_stop_the_server(self) -> None:
        self.notes = os.path.join(self.notes, "not-there-yet")
        responses, stderr, code = self.session(HANDSHAKE + [tool_call(110, "meetings_search", {"query": "anything"})])
        answers = self.by_id(responses)
        self.assertIn("result", answers[1])
        self.assertEqual(json.loads(answers[110]["result"]["content"][0]["text"])["total"], 0, stderr)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
