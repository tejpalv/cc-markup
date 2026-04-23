---
name: cc-markup
description: Measure the real-world tokenizer cost markup between two Claude models on the user's own Claude Code session data. Use when the user asks how much more/less expensive a newer Claude version is on THEIR actual workload, wants to quantify a tokenizer change across their sessions, or wants to replicate published tokenizer-comparison results (e.g. Simon Willison's Opus 4.7 vs 4.6 analysis) on their own data.
argument-hint: "[--n SESSIONS] [--models NEW,OLD] [--max-chars N]"
allowed-tools: Bash(python3 *) Bash(ls *) Read
model: sonnet
---

# cc-markup — measure tokenizer cost markup on your sessions

Runs a bundled Python script that picks the N most recent Claude Code sessions,
calls Anthropic's free `count_tokens` endpoint against two model IDs on each,
and reports the per-session + weighted token-count ratio.

Free in the common case: extracts the user's Claude Code OAuth token from the
macOS Keychain and sends it with the `anthropic-beta: oauth-2025-04-20` header,
which Anthropic's API accepts for `count_tokens`. If that path fails (non-macOS,
not signed in, or the beta behavior changes), falls back to `ANTHROPIC_API_KEY`.
If neither is available, prints a clear fix message and exits.

## How to run

Default (50 sessions, Opus 4.7 vs Opus 4.6):

```bash
python3 scripts/run.py
```

With args:

```bash
python3 scripts/run.py --n 100 --models claude-sonnet-4-7,claude-sonnet-4-6
python3 scripts/run.py --max-chars 200000
```

The script prints a complete markdown report (table + totals + caveats) to
stdout. Relay it as-is, or wrap with extra interpretation.

## When invoked

1. Run the script with the user's args (or defaults).
2. Print the script's stdout directly — it is already formatted markdown.
3. Add a short interpretation at the end tailored to the ratio:
   - If weighted markup > 1.10×: note the cost impact (markup − 1) × 100 % more expensive at flat pricing, and mention prompt caching as a partial hedge.
   - If < 0.95×: note the savings.
   - If between: call it roughly flat.
4. Don't repeat the "tool blocks are stripped" caveat — the script already prints it.

## Failure modes worth passing through

- **Both auth paths failed**: the script prints a ready-made fix message; relay it.
- **Rate limited (HTTP 429) repeatedly**: the script auto-retries 4× with exponential backoff before raising. Suggest smaller `--n` or a minute's wait.
- **Model ID rejected**: Anthropic returns an `invalid_request_error`. Point the user at model IDs on platform.claude.com/docs and `--models NEW,OLD`.

## Out of scope

- Does NOT include tool_use/tool_result content. Those are stripped before counting. The reported markup reflects dialog text and overstates the impact on agent traces heavy in JSON tool output. If the user wants the "true bill" markup, suggest modifying `extract_text` to keep tool blocks.
- Does NOT charge the user's Anthropic account (count_tokens is free on both auth paths).
- Does NOT test Sonnet/Haiku tokenizers separately — if the user passes those model IDs and the underlying tokenizer is the same family, the ratio will be ~1.00×, which is itself a useful signal.
