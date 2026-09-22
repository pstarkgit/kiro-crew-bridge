"""Offline tests for the local Kiro Crew bridge; no gateway or model required."""
import importlib.util
import io
import json
import os
import tempfile
import urllib.error
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).parents[1] / "scripts" / "bridge.py"
spec = importlib.util.spec_from_file_location("crew_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.body


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.cwd = Path(self.temp.name) / "work"
        self.cwd.mkdir()
        self.state = self.home / ".local/state/codex-kiro-crew"
        self.home_patcher = patch.object(bridge, "HOME", self.home)
        self.state_patcher = patch.object(bridge, "STATE", self.state)
        self.home_patcher.start()
        self.state_patcher.start()

    def tearDown(self):
        self.home_patcher.stop()
        self.state_patcher.stop()
        self.temp.cleanup()

    def args(self, **overrides):
        value = {"request_id": "work_1", "task": "Inspect one file.",
                 "model": "grok-4.6", "cwd": str(self.cwd),
                 "max_turns": 3, "authorized": True}
        value.update(overrides)
        return value

    def test_request_uses_private_local_credential_and_disables_proxy(self):
        credential = self.home / ".kiro/crew/run/gateway-5476.secret"
        credential.parent.mkdir(parents=True)
        credential.write_text("not-a-real-secret")
        credential.chmod(0o600)
        captured = {}

        class Opener:
            def open(self, request, timeout):
                captured["url"] = request.full_url
                captured["secret"] = request.get_header("X-internal-secret")
                captured["timeout"] = timeout
                return FakeResponse(b'{"ok":true}')

        with patch.object(bridge.urllib.request, "build_opener", return_value=Opener()) as opener:
            self.assertEqual(bridge.request("/api/spawn"), {"ok": True})
        self.assertEqual(captured["url"], "http://127.0.0.1:5476/api/spawn")
        self.assertEqual(captured["secret"], "not-a-real-secret")
        self.assertEqual(captured["timeout"], 15)
        self.assertIsInstance(opener.call_args.args[0], bridge.urllib.request.ProxyHandler)

    def test_request_refuses_insecure_credential_file(self):
        credential = self.home / ".kiro/crew/.local_secret"
        credential.parent.mkdir(parents=True)
        credential.write_text("not-a-real-secret")
        credential.chmod(0o644)
        with self.assertRaisesRegex(bridge.BridgeError, "private"):
            bridge.request("/api/spawn")

    def test_http_error_exposes_only_safe_gateway_code(self):
        credential = self.home / ".kiro/crew/.local_secret"
        credential.parent.mkdir(parents=True)
        credential.write_text("not-a-real-secret")
        credential.chmod(0o600)

        class Opener:
            def open(self, *_, **__):
                raise urllib.error.HTTPError("http://127.0.0.1", 400, "bad", {},
                                             io.BytesIO(b'{"code":"invalid_cwd","error":"private detail"}'))

        with patch.object(bridge.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(bridge.BridgeError, r"HTTP 400; code=invalid_cwd") as error:
                bridge.request("/api/spawn")
        self.assertNotIn("private detail", str(error.exception))

    def test_health_uses_uncredentialed_readiness_probe(self):
        with patch.object(bridge, "request", return_value={"ready": True}) as request:
            response = bridge.call("crew_health", {})
        self.assertTrue(response["reachable"])
        request.assert_called_once_with("/api/ready", authenticated=False)

    def test_validation_refuses_unsafe_or_unauthorized_dispatch(self):
        with self.assertRaisesRegex(bridge.BridgeError, "authorized"):
            bridge.dispatch(self.args(authorized=False))
        with self.assertRaisesRegex(bridge.BridgeError, "bounded"):
            bridge.dispatch(self.args(cwd="/"))
        with self.assertRaisesRegex(bridge.BridgeError, "identifier"):
            bridge.dispatch(self.args(request_id="../escape"))
        with self.assertRaisesRegex(bridge.BridgeError, "unsupported"):
            bridge.dispatch(self.args(model="xai/grok-4.6"))

    def test_verified_gateway_default_omits_model_without_substitution(self):
        args = self.args()
        args.pop("model")
        args.update({"use_gateway_default": True,
                     "expected_gateway_model": "xai/grok-4.6"})
        with patch.object(bridge, "gateway_default_model", return_value="xai/grok-4.6"), \
             patch.object(bridge, "request", return_value={"id": "agent_1", "status": "spawned"}) as request:
            receipt = bridge.dispatch(args)
        body = request.call_args.args[1]
        self.assertNotIn("model", body)
        self.assertEqual(receipt["requested_model"], "xai/grok-4.6")

    def test_gateway_default_requires_exact_match_before_request(self):
        args = self.args()
        args.pop("model")
        args.update({"use_gateway_default": True,
                     "expected_gateway_model": "xai/grok-4.6"})
        with patch.object(bridge, "gateway_default_model", return_value="other-model"), \
             patch.object(bridge, "request") as request:
            with self.assertRaisesRegex(bridge.BridgeError, "exactly match"):
                bridge.dispatch(args)
        request.assert_not_called()

    def test_optional_explicit_agent_is_forwarded_without_changing_memory_flags(self):
        with patch.object(bridge, "request", return_value={"id": "agent_1", "status": "spawned"}) as request:
            bridge.dispatch(self.args(agent="kirocrew-worker"))
        body = request.call_args.args[1]
        self.assertEqual(body["agent"], "kirocrew-worker")
        self.assertFalse(body["include_memory"])
        self.assertFalse(body["include_lessons"])
        self.assertFalse(body["include_project"])

    def test_explicit_agent_must_use_gateway_identifier_syntax(self):
        with self.assertRaisesRegex(bridge.BridgeError, "Agent ID"):
            bridge.dispatch(self.args(agent="../not-an-agent"))

    def test_named_crew_is_forwarded_without_template_override(self):
        with patch.object(bridge, "request", return_value={"id": "agent_1", "status": "spawned"}) as request:
            bridge.dispatch(self.args(crew="mAIC"))
        body = request.call_args.args[1]
        self.assertEqual(body["crew"], "mAIC")
        self.assertNotIn("agent", body)

    def test_named_crew_and_template_are_mutually_exclusive(self):
        with self.assertRaisesRegex(bridge.BridgeError, "not both"):
            bridge.dispatch(self.args(agent="kirocrew-worker", crew="mAIC"))

    def test_task_limit_includes_required_safety_prefix(self):
        room = bridge.MAX_TASK_CHARS - len(bridge.TASK_PREFIX)
        with self.assertRaisesRegex(bridge.BridgeError, "safety prefix"):
            bridge.dispatch(self.args(task="a" * (room + 1)))

    def test_idempotency_returns_existing_receipt_without_resubmission(self):
        with patch.object(bridge, "request", return_value={"id": "agent_1", "status": "spawned"}) as request:
            first = bridge.dispatch(self.args())
            again = bridge.dispatch(self.args())
        self.assertEqual(first, again)
        self.assertEqual(request.call_count, 1)
        with self.assertRaisesRegex(bridge.BridgeError, "different task"):
            bridge.dispatch(self.args(task="Different task."))

    def test_ambiguous_dispatch_is_reserved_and_never_retried(self):
        with patch.object(bridge, "request", side_effect=bridge.BridgeError("Gateway unavailable")) as request:
            with self.assertRaisesRegex(bridge.BridgeError, "Gateway unavailable"):
                bridge.dispatch(self.args())
            receipt = bridge.dispatch(self.args())
        self.assertEqual(receipt["state"], "dispatch_uncertain")
        self.assertEqual(request.call_count, 1)

    def test_mcp_protocol_returns_tools_and_safe_tool_error(self):
        initialized = bridge.handle({"method": "initialize"})
        self.assertEqual(initialized["capabilities"], {"tools": {}})
        self.assertEqual(bridge.handle({"method": "ping"}), {})
        tools = bridge.handle({"method": "tools/list"})["tools"]
        self.assertEqual({tool["name"] for tool in tools},
                         {"crew_health", "crew_dispatch", "crew_jobs", "crew_status", "crew_result"})
        response = bridge.handle({"method": "tools/call", "params": {
            "name": "crew_dispatch", "arguments": self.args(authorized=False)}})
        self.assertTrue(response["isError"])
        self.assertIn("authorized", response["content"][0]["text"])

    def test_result_reads_only_completed_owned_receipt_with_bounded_paging(self):
        receipt = {"request_id": "work_1", "fingerprint": "a" * 64,
                   "state": "submitted", "requested_model": "grok-4.6",
                   "model_execution_verified": False, "agent_id": "agent_1"}
        bridge.save(self.state / "work_1.json", receipt)
        payload = {"done": True, "result": "one\ntwo\nthree",
                   "result_meta": {"offset": 4, "returned_lines": 3,
                                   "total_lines": 9, "has_more": True}}
        with patch.object(bridge, "request", return_value=payload) as request:
            result = bridge.call("crew_result", {"request_id": "work_1", "offset": 4, "limit": 10})
        request.assert_called_once_with("/api/spawn/agent_1?offset=4&limit=10")
        self.assertEqual(result["result"], "one\ntwo\nthree")
        self.assertEqual(result["result_meta"]["total_lines"], 9)
        self.assertIn("untrusted", result["warning"])

    def test_result_rejects_not_done_and_invalid_page_size(self):
        receipt = {"request_id": "work_1", "fingerprint": "a" * 64,
                   "state": "submitted", "requested_model": "grok-4.6",
                   "model_execution_verified": False, "agent_id": "agent_1"}
        bridge.save(self.state / "work_1.json", receipt)
        with self.assertRaisesRegex(bridge.BridgeError, "limit"):
            bridge.call("crew_result", {"request_id": "work_1", "limit": 101})
        with patch.object(bridge, "request", return_value={"done": False}):
            with self.assertRaisesRegex(bridge.BridgeError, "not ready"):
                bridge.call("crew_result", {"request_id": "work_1"})

    def test_mcp_parse_error_is_a_jsonrpc_error(self):
        original_stdin = bridge.sys.stdin
        original_argv = bridge.sys.argv
        bridge.sys.stdin = io.StringIO("not json\n")
        bridge.sys.argv = ["bridge.py", "--mcp"]
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                bridge.main()
        finally:
            bridge.sys.stdin = original_stdin
            bridge.sys.argv = original_argv
        response = json.loads(output.getvalue())
        self.assertEqual(response["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
