#!/usr/bin/env python3
"""Text checks for prepush.sh. Standard library only.

Runs the house text rules that no existing repo tool covers, on exactly the
surfaces an agent sends out: lines a branch adds, commit messages, the branch
name, and the pull request title and body.

Output names a surface, a line number and a rule id. It never prints the
matched text, because a personal path or a private name in a log is the same
leak the check exists to stop.

Modes:
  diff BASE HEAD            added lines in BASE...HEAD (dash rule skips fenced
                            blocks and frontmatter in prose files), plus Status cells in
                            changed README and docs tables
  text LABEL FILE           a commit subject, branch name or PR title
  body FILE --base SHA      a pull request body (shape, length, strip line)

Exit 0 clean, 1 at least one FAIL, 2 usage or git error. WARN never fails;
prepush.sh lists warnings for reviewers to judge.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EM = "\u2014"
EN = "\u2013"
PROSE_EXT = {".md", ".markdown", ".rst", ".txt", ".cff", ".svg", ".html"}
STATUS_LABELS = {
    "built and tested",
    "wired into a hook",
    "enforced in ci",
    "untested live",
}
BODY_MAX_WORDS = 400
BODY_SECTIONS = ["base", "what changed", "why", "proof", "known limits"]

# Rules for every added line, any file type.
ALL_RULES = [
    ("WIN-HOME-FWD", "FAIL", re.compile(r"(?i)\b[A-Z]:/Users/[^/\s\"'`]+")),
    ("MAC-HOME", "FAIL", re.compile(r"(?<![\w.:])/Users/[A-Za-z0-9._-]+")),
    # Assembled from parts so this file does not match its own rule.
    ("BOX-PATH", "FAIL", re.compile(r"(?<![\w.])/" + "workspace/|agent-" + "tools/")),
    ("TILDE-HOME", "WARN", re.compile(r"(?:^|[\s\"'(=`])~[/\\][A-Za-z]")),
    (
        "ENV-HOME",
        "WARN",
        re.compile(r"\$HOME\b|\$\{HOME\}|%USERPROFILE%|\$env:USERPROFILE", re.I),
    ),
]

# Claim rules for prose surfaces: prose files, commit messages, PR text.
CLAIM_RULES = [
    ("CLAIM-NOVEL", "FAIL", re.compile(r"\bnovel\b", re.I)),
    (
        "CLAIM-FIRST-ONLY",
        "FAIL",
        re.compile(
            r"\b(?:the\s+)?(?:first|only)\s+(?:open[- ]source\s+|public\s+)?"
            r"(?:tool|system|framework|project|implementation|approach|gate|"
            r"engine|library|one|solution|layer|platform)\b"
            r"|\bworld'?s\s+first\b|\bindustry[- ]first\b|\bfirst[- ]of[- ]its[- ]kind\b",
            re.I,
        ),
    ),
    ("CLAIM-SDVOSB", "FAIL", re.compile(r"\bSDVOSB\b|service[- ]disabled veteran[- ]owned", re.I)),
    (
        "RANK",
        "FAIL",
        re.compile(
            r"\b(?:Petty Officer|Chief Petty Officer|Master Chief|Senior Chief|"
            r"Seaman|E-[1-9])\b"
        ),
    ),
    ("ONLY-WORD", "WARN", re.compile(r"\bonly\b", re.I)),
    ("FIRST-WORD", "WARN", re.compile(r"\bfirst\b", re.I)),
]
# ---- Dash rule: Landen's ruling (2026-08-18, reaffirmed 2026-10-19) ---------
#
# A dash used as a GRAMMATICAL BREAK in a sentence is banned. A dash used for
# FORMATTING is allowed: after a heading, inside a table row, after a short
# list label, after a bold, code or link label, or in a key: value line. The
# logic below is copied from voice_lint.py (checks/voice_lint.py here, and
# .floor/craft/voice_lint.py in each repo) so prepush and the voice check
# agree; tests/test_prepush_text.py checks the copies still match.
# The same test is applied to the en dash. An en dash between two numbers (a
# range such as 8, en dash, 15) is flagged everywhere, because the house rules allow no
# en dash range: write "8 to 15" or use a hyphen.
_HEADING = re.compile(r"^\s*#{1,6}\s")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_FM_KEY = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_-]*:\s")
_LABEL_MAX = 60
_RANGE = re.compile(r"\d\s*" + EN + r"\s*\d")


def _markup_label(dash: str) -> re.Pattern[str]:
    return re.compile(
        r"^\s*(?:\*\*.+?\*\*|__.+?__|`.+?`|\[.+?\]\([^)]*\))\s*" + dash
    )


def _strip_label_spans(line: str) -> str:
    line = re.sub(r"\*\*.+?\*\*", "", line)
    line = re.sub(r"__.+?__", "", line)
    line = re.sub(r"`[^`]*`", "", line)
    return line


def dash_is_formatting(line: str, dash: str = EM) -> bool:
    """True when the dash on this line is structural, not grammatical.

    Same decision as voice_lint._emdash_is_formatting, parameterised on the
    dash character so the en dash gets the identical test.
    """
    if dash not in _strip_label_spans(line):
        return True
    if _HEADING.match(line):
        return True
    if "|" in line:
        return True
    if _FM_KEY.match(line):
        return True
    if _markup_label(dash).match(line):
        return True
    m = _LIST_MARK.match(line)
    if m:
        label = line[m.end() :].split(dash, 1)[0].strip()
        label = re.sub(r"\]\([^)]*\)", "]", label)
        label = re.sub(r"`[^`]*`", "", label)
        if len(label) <= _LABEL_MAX and not re.search(r"[.!?]", label):
            return True
    return False


def dash_hits(line: str) -> list[tuple[str, str]]:
    out = []
    if EN in line and _RANGE.search(_strip_label_spans(line)):
        out.append(("FAIL", "ENDASH-RANGE"))
    for dash, rid in ((EM, "EMDASH-BREAK"), (EN, "ENDASH-BREAK")):
        probe = _RANGE.sub("", line) if dash == EN else line
        if dash in probe and not dash_is_formatting(probe, dash):
            out.append(("FAIL", rid))
    return out


def fenced_lines(path: str) -> set[int]:
    """Line numbers inside fenced blocks or leading frontmatter of a prose file.

    voice_lint skips these (a fence is a quotation, not an assertion), so the
    dash check skips them too.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    skip: set[int] = set()
    in_fence = in_fm = False
    for i, line in enumerate(text.splitlines(), 1):
        st = line.strip()
        if i == 1 and st == "---":
            in_fm = True
            skip.add(i)
            continue
        if in_fm:
            skip.add(i)
            if st in ("---", "..."):
                in_fm = False
            continue
        if st.startswith(("```", "~~~")):
            in_fence = not in_fence
            skip.add(i)
            continue
        if in_fence:
            skip.add(i)
    return skip


# The claim token is assembled from parts so this file passes the kit
# pre-commit claim rule; the regex and the rule label are unchanged.
SOC_CLAIM = re.compile(r"SOC\s?-?2", re.I)
SOC_OK = "audit trail toward SOC 2"
SOC_RULE = "CLAIM-SOC" + "2"
GNUM = re.compile(r"\bG-?[0-9]+\b")


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"prepush_text: git {args[0]} failed (exit {r.returncode})", file=sys.stderr)
        raise SystemExit(2)
    return r.stdout


def is_prose(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in PROSE_EXT or p.name.upper().startswith(("README", "CHANGELOG"))


def check_line(label: str, n: int, line: str, prose: bool, out: list, dashes: bool = True) -> None:
    if dashes:
        for sev, rid in dash_hits(line):
            out.append((sev, f"{label}:{n}", rid))
    for rid, sev, rx in ALL_RULES:
        if rx.search(line):
            out.append((sev, f"{label}:{n}", rid))
    if not prose:
        return
    for rid, sev, rx in CLAIM_RULES:
        if rx.search(line):
            out.append((sev, f"{label}:{n}", rid))
    if SOC_CLAIM.search(line) and SOC_OK not in line:
        out.append(("FAIL", f"{label}:{n}", SOC_RULE))
    if GNUM.search(line):
        out.append(("WARN", f"{label}:{n}", "GNUMBER-IN-PROSE"))


def status_cells(path: Path, out: list) -> None:
    """Every table with a Status column uses only the four labels."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    col = None
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            col = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if col is None:
            low = [c.lower().strip("*_ ") for c in cells]
            col = low.index("status") if "status" in low else -1
            continue
        if col < 0 or set(s) <= set("|-: "):
            continue
        if col < len(cells):
            val = re.sub(r"[*_`]", "", cells[col]).strip().lower()
            if val and val not in STATUS_LABELS:
                out.append(("FAIL", f"{path}:{n}", "STATUS-LABEL"))


def mode_diff(base: str, head: str) -> list:
    out: list = []
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", f"{base}...{head}")
    path, n, skip = None, 0, set()
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            t = raw[4:]
            path = None if t == "/dev/null" else t[2:] if t.startswith("b/") else t
            skip = fenced_lines(path) if path and is_prose(path) else set()
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            n = int(m.group(1)) if m else 0
            continue
        if path and raw.startswith("+") and not raw.startswith("+++"):
            check_line(path, n, raw[1:], is_prose(path), out, dashes=n not in skip)
            n += 1
    names = git("diff", "--name-only", "--diff-filter=AMR", f"{base}...{head}").split("\n")
    for name in filter(None, names):
        up = Path(name).name.upper()
        if up.startswith("README") or (name.startswith("docs/") and name.endswith(".md")):
            status_cells(Path(name), out)
        check_line("path", 0, name, False, out)
    return out


def mode_text(label: str, file: str) -> list:
    out: list = []
    text = Path(file).read_text(encoding="utf-8", errors="replace")
    for n, line in enumerate(text.splitlines(), 1):
        check_line(label, n, line, True, out)
        if GNUM.search(line):
            out.append(("FAIL", f"{label}:{n}", "GNUMBER"))
    return out


def mode_body(file: str, base: str) -> list:
    out = mode_text("pr-body", file)
    text = Path(file).read_text(encoding="utf-8", errors="replace")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    words = len(re.findall(r"\S+", prose))
    if words > BODY_MAX_WORDS:
        out.append(("FAIL", "pr-body", f"BODY-LENGTH {words} words, limit {BODY_MAX_WORDS}"))
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if not re.match(r"^[#*\s]*scope vs ask", first, re.I):
        out.append(("FAIL", "pr-body:1", "BODY-FIRST-LINE must be Scope vs ask"))
    low = text.lower()
    for sec in BODY_SECTIONS:
        if not re.search(rf"^[#*\s]*{re.escape(sec)}\b", low, re.M):
            out.append(("FAIL", "pr-body", f"BODY-SECTION missing: {sec}"))
    if base and base[:7].lower() not in low:
        out.append(("FAIL", "pr-body", "BODY-BASE-SHA does not match the merge base"))
    if re.search(r"\bfix(?:es|ed)?\b", low) and not re.search(r"^[#*\s-]*strip\b", low, re.M):
        out.append(("FAIL", "pr-body", "STRIP-MISSING: a fix is claimed with no Strip line"))
    if re.search(r"\bround\s+\d+\b|\br\d+\b|\bprevious head\b|\bearlier round\b", low):
        out.append(("WARN", "pr-body", "ROUND-HISTORY belongs in receipts/"))
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "diff":
        out = mode_diff(argv[1], argv[2])
    elif len(argv) == 3 and argv[0] == "text":
        out = mode_text(argv[1], argv[2])
    elif len(argv) >= 2 and argv[0] == "body":
        base = argv[3] if len(argv) >= 4 and argv[2] == "--base" else ""
        out = mode_body(argv[1], base)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for sev, where, rid in out:
        print(f"{sev} {where} {rid}")
    return 1 if any(s == "FAIL" for s, _, _ in out) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
