"""No external calls: metrics content, privacy, auth, context and failure contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import importlib.util
import io
import os
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observability import Operations, OperationsUnavailable, FIELDS


def configured():
    return {"OPS_ENABLED": "1", "OPS_READ_TOKEN": "t" * 40,
            "UPSTASH_REDIS_REST_URL": "https://example.invalid", "UPSTASH_REDIS_REST_TOKEN": "test-only"}


class ObservabilityTests(unittest.TestCase):
    def test_disabled_has_no_event_or_transport_and_short_secret_never_authorizes(self):
        calls = []
        ops = Operations({}, lambda *args: calls.append(args))
        self.assertIsNone(ops.begin_request("t" * 40))
        self.assertIsNone(ops.finish_request(None))
        self.assertFalse(ops.is_authorized(""))
        ops.values["OPS_READ_TOKEN"] = "short"
        self.assertFalse(ops.is_authorized("short"))
        self.assertEqual(calls, [])

    def test_actual_usage_one_write_and_ticket_never_persists_raw_content_or_identity(self):
        sent = []
        def transport(url, payload, token, timeout):
            sent.append((url, payload, timeout))
            return {"result": 1}
        ops = Operations(configured(), transport)
        event = ops.begin_request()
        self.assertIs(ops.current_event(), event)
        ops.record_usage(event, {"prompt_tokens": 123, "completion_tokens": 45, "raw_question": "private question"})
        ops.record_usage(event, None)
        ops.record_usage(event, {"prompt_tokens": True, "completion_tokens": 2})
        ticket = ops.finish_request(event, 200, "answered")
        self.assertEqual(len(ticket), 43)
        self.assertIsNone(ops.current_event())
        self.assertIsNone(ops.finish_request(event))
        self.assertEqual(len(sent), 1)
        command = sent[0][1]
        self.assertEqual(command[:1], ["EVAL"])
        self.assertEqual(command[2], 2)
        count = int(command[5])
        counters = dict(zip(command[6:6 + count * 2:2], command[7:6 + count * 2:2]))
        self.assertEqual(counters["prompt_tokens"], "123")
        self.assertEqual(counters["output_tokens"], "45")
        self.assertEqual(counters["usage_missing"], "2")
        self.assertEqual(counters["provider_responses"], "3")
        self.assertEqual(counters["answered"], "1")
        self.assertEqual(command[-1], "visitor")
        self.assertNotIn(ticket, json.dumps(command))
        self.assertNotIn("private question", json.dumps(command))
        self.assertEqual(sent[0][2], 0.75)

    def test_signed_synthetic_context_is_thread_local_and_restores_outer_context(self):
        ops = Operations(configured(), lambda *args: {"result": 0})
        outer = ops.begin_request()
        def work(header):
            event = ops.begin_request(header)
            scope = ops.current_event().scope
            ops.finish_request(event, 503, "error")
            return scope, ops.current_event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(work, ["t" * 40, "client-says-synthetic"]))
        self.assertEqual(results, [("synthetic", None), ("visitor", None)])
        inner = ops.begin_request("t" * 40)
        ops.finish_request(inner, 200, "refused")
        self.assertIs(ops.current_event(), outer)
        ops.finish_request(outer, 503, "error")
        self.assertIsNone(ops.current_event())

    def test_failed_write_cannot_break_answer_and_always_resets_context(self):
        def fail(*args):
            raise OSError("secret-content-and-token")
        ops = Operations(configured(), fail)
        event = ops.begin_request()
        self.assertIsNone(ops.finish_request(event))
        self.assertIsNone(ops.current_event())

    def test_refusal_and_errors_never_issue_feedback_ticket(self):
        commands = []
        ops = Operations(configured(), lambda url, command, *args: commands.append(command) or {"result": 1})
        for status, outcome in [(200, "refused"), (429, "error"), (503, "error")]:
            self.assertIsNone(ops.finish_request(ops.begin_request(), status, outcome))
            self.assertEqual(commands[-1][-1], "")

    def test_snapshot_bounded_allowlisted_aggregate_fields_and_explicit_cost_only(self):
        sent = []
        def transport(url, commands, *args):
            sent.append((url, commands))
            return [{"result": ["queries", "2", "prompt_tokens", "1000000", "output_tokens", "500000", "unknown-private-field", "secret"]}] + [{"result": []} for _ in commands[1:]]
        ops = Operations(configured(), transport)
        report = ops.snapshot()
        self.assertEqual(len(sent[0][1]), 14)
        self.assertTrue(sent[0][0].endswith("/pipeline"))
        self.assertTrue(all(command[0] == "HGETALL" and ":daily:" in command[1] for command in sent[0][1]))
        self.assertEqual(report["totals"]["visitor"]["queries"], 2)
        self.assertEqual(report["totals"]["synthetic"]["queries"], 0)
        self.assertIsNone(report["cost"]["estimated_usd"])
        self.assertNotIn("secret", json.dumps(report))
        ops.values.update(OPS_PROMPT_USD_PER_MILLION="0.50", OPS_OUTPUT_USD_PER_MILLION="2")
        self.assertEqual(ops.snapshot()["cost"]["estimated_usd_for_recorded_usage"]["visitor"], "1.50")
        with self.assertRaises(ValueError):
            ops.snapshot(8)

    def test_snapshot_rejects_unbounded_or_non_numeric_values_without_leaking(self):
        for raw in (["queries", "secret"], ["queries"], ["queries", -1], ["queries", "1"] * (len(FIELDS) + 1)):
            ops = Operations(configured(), lambda *args: [{"result": raw}, {"result": []}])
            with self.assertRaises(OperationsUnavailable) as error:
                ops.snapshot(1)
            self.assertNotIn("secret", str(error.exception))

    def test_feedback_validates_before_io_and_maps_atomic_results(self):
        sent = []
        result = [1]
        ops = Operations(configured(), lambda url, command, *args: sent.append(command) or {"result": result[0]})
        self.assertEqual(ops.feedback("bad", True, "clear"), {"status": "invalid"})
        self.assertEqual(ops.feedback("t" * 43, True, "free text is forbidden"), {"status": "invalid"})
        self.assertEqual(sent, [])
        for code, expected in [(1, "accepted"), (0, "expired_or_used"), (-1, "limited")]:
            result[0] = code
            self.assertEqual(ops.feedback("t" * 43, False, "citation_issue"), {"status": expected})
        command = sent[-1]
        self.assertEqual(command[2], 4)
        self.assertIn(":ticket:", command[3])
        self.assertNotIn("t" * 43, json.dumps(command))
        self.assertEqual(command[-2:], ["feedback_unhelpful", "feedback_category_citation_issue"])

    def test_collector_discards_free_text_and_rejects_non_numeric_aggregates(self):
        path = Path(__file__).resolve().parents[2] / "operations/collect.py"
        spec = importlib.util.spec_from_file_location("operations_collect", path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        body = {"schema_version": 1, "window_days": 1, "private-question": "must not persist",
                "daily": [{"date_utc": "2026-10-24", "scope": "visitor", "counts": {"queries": 2}, "answer": "private"}],
                "totals": {"visitor": {"queries": 2}, "synthetic": {"queries": 0}}}
        clean = collector.safe_snapshot(body)
        self.assertEqual(clean["totals"]["visitor"]["queries"], 2)
        self.assertNotIn("private", json.dumps(clean))
        body["totals"]["visitor"]["queries"] = "private"
        with self.assertRaises(ValueError):
            collector.safe_snapshot(body)

    def test_transport_never_follows_redirect_with_secret_header(self):
        path = Path(__file__).resolve().parents[2] / "operations/collect.py"
        spec = importlib.util.spec_from_file_location("operations_collect", path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        self.assertIsNone(collector.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://untrusted.invalid"))

    def test_collector_records_unavailable_state_without_secret_or_fake_zero_totals(self):
        path = Path(__file__).resolve().parents[2] / "operations/collect.py"
        spec = importlib.util.spec_from_file_location("operations_collect", path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            with patch.dict(os.environ, {"OPS_READ_TOKEN": "do-not-persist-this-secret-token-123456789"}), patch.object(collector, "fetch", side_effect=[
                    ({"http_status": 200}, {"status": "ready", "raw_answer": "private content"}),
                    ({"http_status": 503, "error": "endpoint_unavailable"}, None)]), redirect_stdout(io.StringIO()):
                code = collector.main(["--base-url", "https://example.invalid", "--output", str(output)])
            self.assertEqual(code, 1)
            text = output.read_text()
            report = json.loads(text)
            self.assertFalse(report["passed"])
            self.assertEqual(report["operations"]["http_status"], 503)
            self.assertNotIn("aggregates", report)
            self.assertNotIn("private content", text)
            self.assertNotIn("do-not-persist", text)

    def test_collector_missing_token_rejects_before_any_request_or_artifact(self):
        path = Path(__file__).resolve().parents[2] / "operations/collect.py"
        spec = importlib.util.spec_from_file_location("operations_collect", path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            with patch.dict(os.environ, {"OPS_READ_TOKEN": ""}), patch.object(collector, "fetch") as fetch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    collector.main(["--base-url", "https://example.invalid", "--output", str(output)])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())
            fetch.assert_not_called()

    def test_public_health_artifact_validates_but_never_saves_private_aggregate_numbers(self):
        path = Path(__file__).resolve().parents[2] / "operations/collect.py"
        spec = importlib.util.spec_from_file_location("operations_collect", path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        private = {"schema_version": 1, "window_days": 1,
                   "daily": [{"date_utc": "2026-10-24", "scope": "visitor", "counts": {
                       "queries": 918273, "prompt_tokens": 564738, "feedback_helpful": 192837}}],
                   "totals": {"visitor": {"queries": 918273, "prompt_tokens": 564738, "feedback_helpful": 192837}, "synthetic": {}},
                   "cost": {"rates_usd_per_million": {"prompt": "12.345", "output": "67.890"},
                            "estimated_usd_for_recorded_usage": {"visitor": "12345.6789", "synthetic": "0"}}}
        with tempfile.TemporaryDirectory() as temporary:
            for valid, expected_code in ((True, 0), (False, 1)):
                body = private if valid else dict(private, totals={"visitor": {"queries": "bad"}})
                output = Path(temporary) / f"snapshot-{valid}.json"
                with patch.dict(os.environ, {"OPS_READ_TOKEN": "test-secret-123456789012345678901234567890"}), patch.object(collector, "fetch", side_effect=[
                        ({"http_status": 200}, {"status": "ready"}), ({"http_status": 200}, body)]) as fetch, redirect_stdout(io.StringIO()):
                    code = collector.main(["--base-url", "https://example.invalid", "--public-health-only", "--output", str(output)])
                self.assertEqual(code, expected_code)
                self.assertEqual(fetch.call_count, 2)
                text = output.read_text()
                report = json.loads(text)
                self.assertIs(report["aggregate_schema_valid"], valid)
                self.assertIs(report["passed"], valid)
                self.assertNotIn("aggregates", report)
                for forbidden in ("queries", "prompt_tokens", "feedback_helpful", "cost", "918273", "564738", "192837", "12.345", "67.890", "12345.6789"):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
