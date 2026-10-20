<!-- Under 400 words outside code blocks. Rewrite, do not append, when the head changes. Round history goes in receipts/ and is linked. prepush.sh --body checks the shape. -->

Scope vs ask: <what was asked>. <Anything touched beyond it, or "Nothing beyond it.">

Base: <7+ char base SHA> (origin/<default>). Head: <7+ char head SHA>. Verdicts on this head: <role: LAND or HOLD, ...>. Blocking: <what, or nothing>.

## What changed
- <one line per change, in reader's nouns>

## Why
<one to three sentences: the hole or gap, in plain terms>

## Proof
- `<exact command>`: <counts, for example 980 passed, 1 skipped, 11 xfailed>
- Strip: revert `<line or function>`; <N> tests fail (<names>). Restored.
- CI run <run id>: <checks and status>

## Status
| Control | Status |
|---|---|
| <control> | built and tested |

## Known limits
- <what this does not cover, stated as scope>
- <any guard that is red on purpose, and why it stays red>

Round history: receipts/<file>.md
