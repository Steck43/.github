# .github

Shared checks and default templates for the Steck43 repositories.

## Contents
- `.github/workflows/house-checks.yml`: a reusable workflow with four jobs. Each runs on the pull request range:
  - `house (dashes, paths and claims)`: added lines must not use a dash as a sentence break, an en dash range, a personal or home path, or a claim the house rules ban.
  - `house (title and body)`: the pull request title and body pass the same text checks, and the body has the required shape and length.
  - `house (commit subject and authorship)`: every commit uses the account email and carries no AI trailer, and its message passes the text checks.
  - `house (voice)`: `voice_lint.py` on the commit range and the prose files it touches.
- `checks/`: the scripts those jobs run (`prepush_text.py`, `voice_lint.py`, `no_ai_trailers.py`) and `prepush.sh`, which runs the same checks locally before a push.
- `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE/`: defaults for any repo without its own.

## Use from a repo

```yaml
jobs:
  house:
    uses: Steck43/.github/.github/workflows/house-checks.yml@<sha>
    with:
      checks-ref: <the same sha>
```

Pin both lines to one commit SHA. A change here reaches a repo only when that repo moves its pin in its own pull request.

## Status
| Control | Status |
|---|---|
| Shared text, commit and voice jobs | built and tested |
| Called from each repo | untested live |

The names scan is not here yet. It needs the denylist passed in by each caller and lands in a later change.
