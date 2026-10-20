#!/usr/bin/env bash
# prepush.sh: one command that decides whether a branch is ready to push.
#
# Run by hand before a push; it is not installed as a hook. It never
# pushes, never fetches with side effects beyond refs, never edits a file in
# the repo, and has no flag that skips a failing check and still says ready.
#
# Usage (run from the repo root, on the PR branch):
#   prepush.sh [--body FILE] [--title TEXT] [--scope FILE] [--no-fetch]
#
#   --body FILE   the PR body you are about to post (checked for shape, length,
#                 base SHA, strip line, leaks, dashes, claims)
#   --title TEXT  the PR title (same text checks)
#   --scope FILE  one glob per line from PLAN.md "Files touched"; any changed
#                 path outside it fails (wrong branch contents)
#   --no-fetch    use the refs you already have (offline)
#
# Environment:
#   LEAK_DENYLIST            denylist text, else .git/info/leak-denylist
#   PREPUSH_LEAK_SCANNER     scanner path when the repo has none (aegis-atoms):
#                            a copy of capability-gate main's
#                            scripts/leak_denylist_scan.py
#   PREPUSH_AUTHOR_EMAIL     required author and committer email
#   PREPUSH_TEST_CMD         test command; default picked per repo
#   PREPUSH_PYTHON           interpreter with the repo's dev requirements
#
# Exit: 0 READY, 1 FAIL, 2 STOP (missing tool, denylist or git error),
#       3 NEEDS LANDEN (a guard, pin, workflow or strict-xfail file changed).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEXT="$HERE/prepush_text.py"
AUTHOR_EMAIL="${PREPUSH_AUTHOR_EMAIL:-106559550+Steck43@users.noreply.github.com}"
BODY="" TITLE="" SCOPE="" FETCH=1

while [ $# -gt 0 ]; do
  case "$1" in
    --body) BODY="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --no-fetch) FETCH=0; shift ;;
    *) echo "prepush: unknown argument $1" >&2; exit 2 ;;
  esac
done

FAILS=0 STOPS=0 OWNER=0 WARNS=0
declare -a REPORT
note() { REPORT+=("$1"); echo "$1"; }
pass() { note "PASS  $1"; }
fail() { note "FAIL  $1"; FAILS=$((FAILS + 1)); }
stop() { note "STOP  $1"; STOPS=$((STOPS + 1)); }
warn() { note "WARN  $1"; WARNS=$((WARNS + 1)); }
owner() { note "OWNER $1"; OWNER=$((OWNER + 1)); }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PY="${PREPUSH_PYTHON:-python3}"

# Run a check command; its own output goes to the report file, not stdout.
runchk() {
  local name="$1"; shift
  if "$@" >"$TMP/out" 2>&1; then pass "$name"
  elif grep -q "ModuleNotFoundError\|No module named\|command not found" "$TMP/out"; then
    stop "$name could not run (missing dependency): $(grep -m1 -o "No module named.*\|command not found.*" "$TMP/out")"
  else fail "$name (see output below)"; sed 's/^/      /' "$TMP/out" | head -40
  fi
}

# ---- 0. where are we ------------------------------------------------------
git rev-parse --show-toplevel >/dev/null 2>&1 || { echo "STOP  not a git repo"; exit 2; }
cd "$(git rev-parse --show-toplevel)" || exit 2
REPO="$(basename "$(git config --get remote.origin.url 2>/dev/null || pwd)" .git)"
BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
[ "$FETCH" = 1 ] && { git fetch --quiet origin || stop "git fetch origin failed"; }
DEFAULT="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
if [ -z "$DEFAULT" ]; then
  for c in main master; do git rev-parse --verify -q "origin/$c" >/dev/null && { DEFAULT="$c"; break; }; done
fi
[ -n "$DEFAULT" ] || { echo "STOP  cannot resolve origin default branch"; exit 2; }
BASE="$(git merge-base HEAD "origin/$DEFAULT")" || { echo "STOP  no merge base"; exit 2; }
HEADSHA="$(git rev-parse HEAD)"
note "repo $REPO  branch ${BRANCH:-DETACHED}  default $DEFAULT"
note "base $(git rev-parse --short "$BASE")  head $(git rev-parse --short "$HEADSHA")  origin/$DEFAULT $(git rev-parse --short "origin/$DEFAULT")"

# ---- 1. branch and base ---------------------------------------------------
if [ -z "$BRANCH" ]; then fail "BRANCH detached HEAD"
elif [ "$BRANCH" = "$DEFAULT" ]; then fail "BRANCH on the default branch; work goes on a PR branch"
else pass "BRANCH $BRANCH is a PR branch"; fi
[ -n "$(git status --porcelain)" ] && fail "TREE uncommitted changes (the push would not match what was checked)"
NCOMMITS="$(git rev-list --count "$BASE..HEAD")"
[ "$NCOMMITS" -gt 0 ] || fail "RANGE no commits ahead of origin/$DEFAULT"
git merge-base --is-ancestor "origin/$DEFAULT" HEAD \
  || warn "BASE origin/$DEFAULT has moved past this branch's base; merge it in (no rebase) and rerun"
STACKED=""
for c in $(git rev-list --no-merges "$BASE..HEAD"); do
  others="$(git branch -r --contains "$c" | sed 's/^ *//' | grep -v -e "^origin/HEAD" -e "^origin/$BRANCH\$" || true)"
  [ -n "$others" ] && STACKED="yes"
done
[ -n "$STACKED" ] && fail "STACK a commit on this branch is also on another remote branch (stacked or duplicate PR)" \
  || pass "STACK branch is cut from origin/$DEFAULT, not from another PR branch"

# ---- 2. commit metadata ---------------------------------------------------
CF_START=$FAILS
TRAILER_HOOK=""
[ -f .githooks/no_ai_trailer.py ] && TRAILER_HOOK=".githooks/no_ai_trailer.py"
for c in $(git rev-list --no-merges "$BASE..HEAD"); do
  s="$(git rev-parse --short "$c")"
  git log -1 --format=%s "$c" >"$TMP/subj"
  git log -1 --format=%B "$c" >"$TMP/msg"
  ae="$(git log -1 --format=%ae "$c")"; ce="$(git log -1 --format=%ce "$c")"
  [ "$ae" = "$AUTHOR_EMAIL" ] || fail "AUTHOR $s author email is not the noreply address"
  [ "$ce" = "$AUTHOR_EMAIL" ] || [ "$ce" = "noreply@github.com" ] || fail "COMMITTER $s committer email is not the noreply address"
  $PY "$TEXT" text "subject:$s" "$TMP/subj" >"$TMP/t" || { fail "SUBJECT $s"; sed 's/^/      /' "$TMP/t"; }
  [ "$(wc -c <"$TMP/subj")" -le 73 ] || warn "SUBJECT $s is over 72 characters"
  if [ -n "$TRAILER_HOOK" ]; then
    $PY -I "$TRAILER_HOOK" "$TMP/msg" >/dev/null 2>&1 || fail "TRAILER $s AI authorship trailer (repo hook)"
  elif grep -Eiq '^\s*(co-authored-by|assisted-by|authored-by):.*(claude|cursor|copilot|gpt|gemini|assistant|bot|ai)\b|generated with' "$TMP/msg"; then
    fail "TRAILER $s AI authorship trailer"
  fi
  # One concern per commit: prose, CI and code never share a commit.
  kinds="$(git diff-tree --no-commit-id --name-only -r "$c" | awk '
    /^CHANGELOG/ {next}
    /^\.github\// {print "ci"; next}
    /(^|\/)README|^docs\/|\.md$|\.svg$/ {print "prose"; next}
    {print "code"}' | sort -u | tr '\n' ' ')"
  [ "$(echo "$kinds" | wc -w)" -gt 1 ] && fail "SPLIT $s mixes ${kinds% }; README, CI and code go in separate commits"
done
CF_BEFORE_DONE=1; [ "$FAILS" -eq "${CF_START:-0}" ] && pass "COMMITS $NCOMMITS commit(s): author, subject, trailers, split"

# ---- 3. scope, guards, strict xfail ---------------------------------------
git diff --name-only --diff-filter=AMRD "$BASE...HEAD" >"$TMP/changed"
if [ -n "$SCOPE" ]; then
  out="$($PY - "$SCOPE" "$TMP/changed" <<'PYEOF'
import fnmatch, sys
globs = [g.strip() for g in open(sys.argv[1]) if g.strip() and not g.startswith("#")]
for p in open(sys.argv[2]).read().split():
    if not any(fnmatch.fnmatch(p, g) for g in globs):
        print(p)
PYEOF
)"
  [ -z "$out" ] && pass "SCOPE every changed path is in the plan" \
    || { fail "SCOPE $(echo "$out" | wc -l) path(s) outside the plan's Files touched"; echo "$out" | sed 's/^/      /'; }
else
  warn "SCOPE no --scope file given; branch contents checked by review only"
fi
GUARDS='^(\.github/|scripts/floor_guard\.py|\.github/floor-pins\.json|requirements-ci\.lock|\.gitleaks\.toml|\.gitleaksignore|scripts/leak_denylist_scan\.py|scripts/public_surface_scan\.py|scripts/no_ai_trailers\.py|\.githooks/|\.floor/|pytest\.ini|conftest\.py|ruff\.toml|\.pre-commit-config\.yaml|SHA256SUMS|scripts/gen_manifest\.sh)'
g="$(grep -E "$GUARDS" "$TMP/changed" || true)"
[ -n "$g" ] && { owner "GUARD-TOUCH these guard or pin files changed and need Landen's explicit yes:"; echo "$g" | sed 's/^/      /'; }
if git diff -U0 "$BASE...HEAD" | grep -E '^-' | grep -Eq 'strict=True|xfail_strict *= *true'; then
  fail "XFAIL a strict xfail or xfail_strict line was removed"
fi

# ---- 4. house text rules on added lines, branch name, PR text -------------
$PY "$TEXT" diff "$BASE" HEAD >"$TMP/t"; rc=$?
grep '^WARN' "$TMP/t" | awk '{print $NF}' | sort | uniq -c | sed 's/^/      WARN count /' >"$TMP/w"
[ "$rc" -eq 0 ] && pass "TEXT added lines: no dash used as a sentence break, no en dash range, personal path, banned claim, bad status label" \
  || { fail "TEXT added lines"; grep '^FAIL' "$TMP/t" | sed 's/^/      /'; }
[ -s "$TMP/w" ] && { warn "TEXT judgment items in added lines (counts by rule)"; cat "$TMP/w"; }
printf '%s\n' "$BRANCH" >"$TMP/branch"
$PY "$TEXT" text branch "$TMP/branch" >/dev/null || fail "TEXT branch name breaks a text rule"
if [ -n "$TITLE" ]; then printf '%s\n' "$TITLE" >"$TMP/title"
  $PY "$TEXT" text title "$TMP/title" >"$TMP/t" || { fail "TEXT PR title"; sed 's/^/      /' "$TMP/t"; }; fi
if [ -n "$BODY" ]; then
  $PY "$TEXT" body "$BODY" --base "$(git rev-parse "$BASE")" >"$TMP/t" \
    && pass "BODY shape, length, base SHA, strip line" || { fail "BODY"; grep -v '^WARN' "$TMP/t" | sed 's/^/      /'; }
else warn "BODY no --body file given; the PR body is unchecked"; fi

# ---- 5. names, home paths, private addresses (existing scanners) ----------
SCANNER=""
[ -f scripts/leak_denylist_scan.py ] && SCANNER="scripts/leak_denylist_scan.py"
[ -z "$SCANNER" ] && [ -n "${PREPUSH_LEAK_SCANNER:-}" ] && SCANNER="$PREPUSH_LEAK_SCANNER"
if [ -z "${LEAK_DENYLIST:-}" ] && [ ! -s "$(git rev-parse --git-dir)/info/leak-denylist" ]; then
  stop "LEAK no denylist (LEAK_DENYLIST or .git/info/leak-denylist). Stop and tell Landen."
elif [ -z "$SCANNER" ]; then
  stop "LEAK no scanner in this repo and PREPUSH_LEAK_SCANNER is unset"
else
  runchk "LEAK commits, messages and file names in range" $PY -I "$SCANNER" --ci --base "$BASE" --head HEAD
  { printf '%s\n' "$BRANCH"; [ -n "$TITLE" ] && printf '%s\n' "$TITLE"; [ -n "$BODY" ] && cat "$BODY"; } >"$TMP/pr"
  $PY -I "$SCANNER" --text "branch, title and body" <"$TMP/pr" >"$TMP/out" 2>&1 \
    && pass "LEAK branch name, PR title and body" || { fail "LEAK branch name, PR title or body"; sed 's/^/      /' "$TMP/out"; }
fi
[ -f scripts/public_surface_scan.py ] && runchk "LEAK public surface scan (whole tree)" $PY -I scripts/public_surface_scan.py --root .

# ---- 6. voice and craft (existing .floor/craft tools) ---------------------
if [ -f .floor/craft/voice_lint.py ]; then
  mapfile -t PROSE < <(git diff --name-only --diff-filter=AMR "$BASE...HEAD" | grep -E '(^|/)(README|CHANGELOG)|\.md$' || true)
  [ -n "$BODY" ] && PROSE+=("$BODY")
  if [ "${#PROSE[@]}" -gt 0 ]; then
    runchk "VOICE commits and changed prose" $PY -I .floor/craft/voice_lint.py --commits --base "$BASE" --head HEAD --paths "${PROSE[@]}" --fail-on-warn
  else
    runchk "VOICE commits" $PY -I .floor/craft/voice_lint.py --commits --base "$BASE" --head HEAD --fail-on-warn
  fi
  [ -f .floor/craft/comment_craft.py ] && runchk "CRAFT comments" $PY -I .floor/craft/comment_craft.py --base "$BASE" --head HEAD
  [ -f .floor/craft/changelog_gate.py ] && runchk "CRAFT changelog" $PY -I .floor/craft/changelog_gate.py --base "$BASE" --head HEAD
  [ -f .floor/craft/empty_diff_gate.py ] && runchk "CRAFT empty diff" $PY -I .floor/craft/empty_diff_gate.py --base "$BASE" --head HEAD
else
  stop "VOICE .floor/craft/voice_lint.py missing in this repo"
fi

# ---- 7. derived files -----------------------------------------------------
[ -f SHA256SUMS ] && runchk "DERIVED SHA256SUMS matches the tree" sha256sum -c --quiet SHA256SUMS
if [ -f scripts/floor_guard.py ] && [ -f .github/floor-pins.json ]; then
  runchk "DERIVED floor guard against this branch's own pins" $PY -I scripts/floor_guard.py --root . --pins .github/floor-pins.json
  note "INFO  CI 'floor (guard from base)' judges against main's pins; red there is expected when pins move. Do not tune it."
fi

# ---- 8. lint and tests ----------------------------------------------------
RUFF=""; command -v ruff >/dev/null && RUFF="ruff"
[ -z "$RUFF" ] && $PY -m ruff --version >/dev/null 2>&1 && RUFF="$PY -m ruff"
if [ -f ruff.toml ] || [ -f pyproject.toml ]; then
  WANT="$(grep -hoE '^ruff==[0-9.]+' requirements-ci.lock requirements-dev.txt 2>/dev/null | head -1 | cut -d= -f3)"
  HAVE="$($RUFF --version 2>/dev/null | awk '{print $2}')"
  if [ -n "$RUFF" ] && [ -n "$WANT" ] && [ "$WANT" != "$HAVE" ]; then
    stop "LINT ruff $HAVE installed but the repo pins $WANT; install the pin, do not lint with another version"
  elif [ -n "$RUFF" ]; then
    runchk "LINT ruff check" $RUFF check .
    runchk "LINT ruff format --check" $RUFF format --check .
  else stop "LINT ruff not installed"; fi
fi
TESTCMD="${PREPUSH_TEST_CMD:-}"
if [ -z "$TESTCMD" ]; then
  case "$REPO" in
    capability-gate) TESTCMD="$PY -m pytest -q -n 4" ;;
    aegis-atoms) TESTCMD="env PYTHONPATH=. $PY -m pytest -q" ;;
    *) TESTCMD="$PY -m pytest -q" ;;
  esac
fi
note "TEST  $TESTCMD"
if $TESTCMD >"$TMP/tests" 2>&1; then
  pass "TESTS $(grep -E '[0-9]+ (passed|failed)' "$TMP/tests" | tail -1)"
else
  fail "TESTS $(grep -E '[0-9]+ (passed|failed|error)' "$TMP/tests" | tail -1)"; tail -15 "$TMP/tests" | sed 's/^/      /'
fi
if [ -f Cargo.toml ]; then
  if command -v cargo >/dev/null; then runchk "TESTS cargo test" cargo test --quiet
  else warn "TESTS cargo missing; cite the CI run ID for Rust instead"; fi
fi

# ---- verdict --------------------------------------------------------------
echo
echo "summary: $FAILS fail, $STOPS stop, $OWNER owner, $WARNS warn"
if [ "$STOPS" -gt 0 ]; then echo "VERDICT STOP: a tool or the denylist is missing. Report it; do not push."; exit 2; fi
if [ "$FAILS" -gt 0 ]; then echo "VERDICT FAIL: fix and rerun. Do not push."; exit 1; fi
if [ "$OWNER" -gt 0 ]; then echo "VERDICT NEEDS LANDEN: guard or pin change. Push only after his explicit yes."; exit 3; fi
echo "VERDICT READY. Push with exactly: git push origin $BRANCH"
echo "Never --force, never --no-verify, never a push that names $DEFAULT."
exit 0
