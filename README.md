# cc-markup

Tokenizer cost markup between Claude model versions, measured on your own Claude Code sessions.

## Install

    npx skills add tejpalv/cc-markup -g -a claude-code -y

Then in Claude Code, type `/cc-markup` or ask "what's my tokenizer cost markup?"

## Example

- **Your personal tokenizer ratio: 1.366×**

  On 50 of your most recent sessions (442,988 chars of user+assistant text):

  - Opus 4.6 total: 324,346 tokens
  - Opus 4.7 total: 442,988 tokens
  - Weighted ratio: 1.3658 → 36.6% more expensive at the same price-per-token
  - Distribution: min 1.282× / median 1.364× / max 1.478×

## License

[MIT](LICENSE)
