#!/usr/bin/env python3
"""cc-markup — measure tokenizer cost ratio between two Claude models on your own sessions.

Picks the N most-recent Claude Code sessions from ~/.claude/projects/, concatenates
user+assistant text from each, and hits Anthropic's free count_tokens endpoint twice
per session (once per model). Reports the per-session and weighted ratio.

Credential resolution order:
  1. Claude Code OAuth token from macOS Keychain (via `security` CLI).
     Sent with `anthropic-beta: oauth-2025-04-20`, which Anthropic accepts for
     count_tokens. If this path fails for any reason, falls back:
  2. ANTHROPIC_API_KEY environment variable (standard x-api-key auth).
  3. Clear fix message on stderr, exit 2.

The count_tokens endpoint is free on both auth paths.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.anthropic.com/v1/messages/count_tokens"
SESSIONS_DIR = pathlib.Path.home() / ".claude" / "projects"
USER_AGENT = "cc-markup/0.1 (+https://github.com/tejpalv/cc-markup)"
DEFAULT_MAX_CHARS = 400_000
SLEEP_BETWEEN_CALLS = 0.15


def resolve_auth() -> tuple[str, dict[str, str]]:
    """Return (mode, headers) where mode is 'oauth' or 'apikey'. Exit 2 on failure."""
    base = {"anthropic-version": "2023-06-01", "content-type": "application/json", "user-agent": USER_AGENT}

    if platform.system() == "Darwin":
        try:
            raw = subprocess.check_output(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
            token = json.loads(raw)["claudeAiOauth"]["accessToken"]
            return "oauth", {
                **base,
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            }
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError):
            pass

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return "apikey", {**base, "x-api-key": key}

    sys.stderr.write(
        "ERROR: could not resolve API credentials.\n\n"
        "cc-markup tries two auth paths:\n"
        "  1. Claude Code OAuth token from the macOS Keychain.\n"
        "     Requires macOS + a signed-in Claude Code install.\n"
        "  2. ANTHROPIC_API_KEY env var.\n\n"
        "Fix by either:\n"
        "  * Running on macOS with Claude Code signed in, OR\n"
        "  * `export ANTHROPIC_API_KEY=sk-ant-...` then re-run.\n"
        "    (Get a key at https://console.anthropic.com — count_tokens is free.)\n"
    )
    sys.exit(2)


def extract_text(jsonl_path: pathlib.Path) -> str:
    """Concatenate user+assistant text from a session JSONL. Skips tool_use / tool_result blocks."""
    parts: list[str] = []
    try:
        raw = jsonl_path.read_text(errors="replace")
    except OSError:
        return ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or rec
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
    return "\n".join(parts)


def format_api_error(status: int, body: bytes, model: str) -> str:
    """Build a redacted one-line error message from an Anthropic error response body.

    Never echoes the raw body — only the structured `error.type` + `error.message` fields
    if the body is valid JSON. Falls back to status code only on any parse failure.
    """
    try:
        parsed = json.loads(body.decode("utf-8"))
        err = parsed.get("error") or {}
        etype = str(err.get("type") or f"http_{status}")[:64]
        emsg = str(err.get("message") or "")[:200]
        if emsg:
            return f"API error on {model}: {etype}: {emsg}"
        return f"API error on {model}: {etype}"
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return f"HTTP {status} on {model}"


def count_tokens(headers: dict[str, str], model: str, text: str) -> int:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(API, data=body, headers=headers, method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return int(json.loads(resp.read())["input_tokens"])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(format_api_error(e.code, e.read(), model))
    raise SystemExit(f"rate-limited repeatedly on {model}")


def pick_sessions(n: int, max_chars: int) -> tuple[list[tuple[pathlib.Path, str]], int]:
    files = sorted(
        (p for p in SESSIONS_DIR.rglob("*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    picked: list[tuple[pathlib.Path, str]] = []
    for p in files:
        text = extract_text(p)
        if len(text) < 200:
            continue
        if len(text) > max_chars:
            text = text[:max_chars]
        picked.append((p, text))
        if len(picked) >= n:
            break
    return picked, len(files)


def render_report(
    picked: list[tuple[pathlib.Path, str]],
    results: list[tuple[int, int]],
    new_model: str,
    old_model: str,
    mode: str,
    total_files: int,
    max_chars: int,
) -> str:
    lines: list[str] = []
    lines.append(f"# cc-markup: `{new_model}` vs `{old_model}`")
    lines.append("")
    lines.append(
        f"Auth: **{mode}** | Sessions: **{len(picked)}** of {total_files} scanned | "
        f"Per-sample cap: {max_chars:,} chars"
    )
    lines.append("")
    new_suffix = new_model.split("-")[-1]
    old_suffix = old_model.split("-")[-1]
    lines.append(f"| # | session | chars | {new_suffix} tok | {old_suffix} tok | ratio |")
    lines.append("|---|---------|------:|------:|------:|------:|")
    total_new = total_old = 0
    ratios: list[float] = []
    for i, ((p, text), (new_count, old_count)) in enumerate(zip(picked, results), 1):
        ratio = new_count / old_count
        total_new += new_count
        total_old += old_count
        ratios.append(ratio)
        lines.append(
            f"| {i} | `{p.name[:36]}` | {len(text):,} | {new_count:,} | {old_count:,} | {ratio:.3f} |"
        )

    ratios_sorted = sorted(ratios)
    mid = len(ratios_sorted) // 2
    median = (
        ratios_sorted[mid]
        if len(ratios_sorted) % 2
        else (ratios_sorted[mid - 1] + ratios_sorted[mid]) / 2
    )
    weighted = total_new / total_old

    lines.append("")
    lines.append(f"**Totals:** {total_new:,} ({new_model}) vs {total_old:,} ({old_model})")
    lines.append("")
    lines.append(f"- **Weighted markup: {weighted:.4f}×** — this is the number that predicts your cost change.")
    lines.append(
        f"- Per-session: min **{ratios_sorted[0]:.3f}×** / median **{median:.3f}×** / max **{ratios_sorted[-1]:.3f}×**"
    )
    lines.append("")
    lines.append(
        "_Caveat: user+assistant text only — tool_use / tool_result blocks are stripped. "
        "Full-prompt ratios on heavy-tool agent traces typically land a bit lower._"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50, help="number of most-recent sessions (default 50)")
    ap.add_argument(
        "--models",
        default="claude-opus-4-7,claude-opus-4-6",
        help="comma-separated MODEL_NEW,MODEL_OLD (default claude-opus-4-7,claude-opus-4-6)",
    )
    ap.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"truncate each sample to this many chars (default {DEFAULT_MAX_CHARS:,})",
    )
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) != 2:
        raise SystemExit("--models must be exactly two comma-separated IDs: NEW,OLD")
    new_model, old_model = models

    if args.n < 1:
        raise SystemExit("--n must be >= 1")
    if args.max_chars < 1_000:
        raise SystemExit("--max-chars must be >= 1000")

    mode, headers = resolve_auth()
    picked, total_files = pick_sessions(args.n, args.max_chars)
    if not picked:
        raise SystemExit("no qualifying sessions found under ~/.claude/projects/")

    results: list[tuple[int, int]] = []
    for _, text in picked:
        new_count = count_tokens(headers, new_model, text)
        time.sleep(SLEEP_BETWEEN_CALLS)
        old_count = count_tokens(headers, old_model, text)
        time.sleep(SLEEP_BETWEEN_CALLS)
        results.append((new_count, old_count))

    print(render_report(picked, results, new_model, old_model, mode, total_files, args.max_chars))
    return 0


if __name__ == "__main__":
    sys.exit(main())
