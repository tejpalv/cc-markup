"""Tests for cc-markup/scripts/run.py. Pure functions only — no network, no keychain."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cc_markup_run", REPO_ROOT / "scripts" / "run.py")
run = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(run)


class ExtractTextTests(unittest.TestCase):
    def _write(self, lines: list[dict]) -> pathlib.Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        for line in lines:
            tmp.write(json.dumps(line) + "\n")
        tmp.close()
        return pathlib.Path(tmp.name)

    def test_string_content(self) -> None:
        path = self._write([
            {"message": {"role": "user", "content": "hello"}},
            {"message": {"role": "assistant", "content": "world"}},
        ])
        self.assertEqual(run.extract_text(path), "hello\nworld")

    def test_block_list_content(self) -> None:
        path = self._write([
            {"message": {"role": "user", "content": [{"type": "text", "text": "ping"}]}},
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "pong"}]}},
        ])
        self.assertEqual(run.extract_text(path), "ping\npong")

    def test_tool_blocks_skipped(self) -> None:
        path = self._write([
            {"message": {"role": "user", "content": [
                {"type": "text", "text": "run tool"},
                {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}},
            ]}},
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "huge output should be skipped"},
            ]}},
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
        ])
        self.assertEqual(run.extract_text(path), "run tool\ndone")

    def test_non_user_assistant_skipped(self) -> None:
        path = self._write([
            {"message": {"role": "system", "content": "should be ignored"}},
            {"message": {"role": "user", "content": "keep"}},
        ])
        self.assertEqual(run.extract_text(path), "keep")

    def test_malformed_lines_tolerated(self) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        tmp.write('{"message": {"role": "user", "content": "first"}}\n')
        tmp.write("not json at all\n")
        tmp.write("\n")
        tmp.write('{"message": {"role": "assistant", "content": "second"}}\n')
        tmp.close()
        self.assertEqual(run.extract_text(pathlib.Path(tmp.name)), "first\nsecond")

    def test_bare_record_without_message_wrapper(self) -> None:
        path = self._write([
            {"role": "user", "content": "bare"},
            {"role": "assistant", "content": "also bare"},
        ])
        self.assertEqual(run.extract_text(path), "bare\nalso bare")

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(run.extract_text(pathlib.Path("/nonexistent/path.jsonl")), "")


class FormatApiErrorTests(unittest.TestCase):
    def test_structured_error(self) -> None:
        body = json.dumps({
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "model: claude-foo not found"},
        }).encode()
        out = run.format_api_error(400, body, "claude-foo")
        self.assertIn("invalid_request_error", out)
        self.assertIn("model: claude-foo not found", out)
        self.assertIn("claude-foo", out)

    def test_non_json_body_no_leak(self) -> None:
        body = b"<html><body>SECRET_TOKEN_abc123 leaked diagnostics</body></html>"
        out = run.format_api_error(502, body, "claude-opus-4-7")
        self.assertNotIn("SECRET_TOKEN", out)
        self.assertNotIn("leaked", out)
        self.assertIn("HTTP 502", out)
        self.assertIn("claude-opus-4-7", out)

    def test_non_utf8_body_no_crash(self) -> None:
        body = b"\xff\xfe\x00\x01 binary garbage"
        out = run.format_api_error(500, body, "m")
        self.assertIn("HTTP 500", out)

    def test_error_body_truncated(self) -> None:
        body = json.dumps({"error": {"type": "x", "message": "A" * 500}}).encode()
        out = run.format_api_error(400, body, "m")
        self.assertLessEqual(len(out), 400)

    def test_etype_truncated(self) -> None:
        body = json.dumps({"error": {"type": "B" * 200, "message": "msg"}}).encode()
        out = run.format_api_error(400, body, "m")
        self.assertLessEqual(out.count("B"), 64)

    def test_empty_error_object(self) -> None:
        body = b'{"error": {}}'
        out = run.format_api_error(400, body, "m")
        self.assertIn("http_400", out)
        self.assertIn("m", out)

    def test_null_body(self) -> None:
        out = run.format_api_error(500, b"", "m")
        self.assertIn("HTTP 500", out)


class RenderReportTests(unittest.TestCase):
    def test_renders_weighted_and_range(self) -> None:
        picked = [
            (pathlib.Path("a.jsonl"), "x" * 1000),
            (pathlib.Path("b.jsonl"), "y" * 2000),
        ]
        results = [(140, 100), (280, 200)]
        out = run.render_report(picked, results, "claude-opus-4-7", "claude-opus-4-6", "oauth", 99, 400_000)
        # weighted = (140+280) / (100+200) = 1.4
        self.assertIn("1.4000×", out)
        self.assertIn("oauth", out)
        self.assertIn("a.jsonl", out)
        self.assertIn("b.jsonl", out)
        self.assertIn("99 scanned", out)


class ArgParsingTests(unittest.TestCase):
    def test_rejects_single_model(self) -> None:
        with self.assertRaises(SystemExit):
            run.main(["--models", "just-one-model"])

    def test_rejects_three_models(self) -> None:
        with self.assertRaises(SystemExit):
            run.main(["--models", "a,b,c"])

    def test_rejects_zero_n(self) -> None:
        with self.assertRaises(SystemExit):
            run.main(["--n", "0"])

    def test_rejects_tiny_max_chars(self) -> None:
        with self.assertRaises(SystemExit):
            run.main(["--max-chars", "100"])


if __name__ == "__main__":
    unittest.main()
