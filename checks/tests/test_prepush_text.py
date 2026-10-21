"""Dash rule in prepush_text.py: grammatical breaks fail, formatting passes.

Dashes are written as escapes so this file passes its own check.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import prepush_text as pt  # noqa: E402

EM, EN = "\u2014", "\u2013"

# Cases from voice_lint.py --selftest, plus Landen's 2026-10-19 example.
BREAKS = [
    f"Ship the floor {EM} then unlock egress.",
    f"The floor {EM} not the judge {EM} decides.",
    "- The floor is CI on someone else's machine, and it only binds once "
    f"it is required {EM} which is a settings flip.",
    f"the gate blocks it {EM} and logs it",
    f"Run `make check`. It passes {EM} mostly.",
]
FORMATTING = [
    f"# Artifact Index {EM} volume 2",
    f"| 2026-08-18 | path | what {EM} detail | LIVE |",
    f"**Floor** {EM} deterministic, non-bypassable",
    f"- gitleaks {EM} secrets, working tree and history",
    f"- [OPEN NOW](open.md) {EM} read this first",
    f"`floor_guard.py` {EM} loads from the base branch",
    f"title: Index {EM} volume 2",
    f"1. Strip test {EM} revert the fix and watch it fail",
    f"Plant `a {EM} b` inside a code span.",
]


@pytest.mark.parametrize("line", BREAKS)
def test_em_dash_break_fails(line):
    assert ("FAIL", "EMDASH-BREAK") in pt.dash_hits(line)


@pytest.mark.parametrize("line", FORMATTING)
def test_em_dash_formatting_passes(line):
    assert pt.dash_hits(line) == []


@pytest.mark.parametrize("line", [b.replace(EM, EN) for b in BREAKS])
def test_en_dash_break_fails(line):
    assert ("FAIL", "ENDASH-BREAK") in pt.dash_hits(line)


@pytest.mark.parametrize("line", [f.replace(EM, EN) for f in FORMATTING])
def test_en_dash_formatting_passes(line):
    assert pt.dash_hits(line) == []


@pytest.mark.parametrize(
    "line",
    [
        f"Caught 8{EN}15 of the cases.",
        f"| pages | 3 {EN} 7 |",
        f"- Run 2026-10-01{EN}2026-10-19",
    ],
)
def test_en_dash_number_range_is_flagged_everywhere(line):
    # The house rules allow no en dash range, so even a table cell is flagged.
    assert ("FAIL", "ENDASH-RANGE") in pt.dash_hits(line)


def test_range_and_break_on_one_line_reports_both():
    hits = pt.dash_hits(f"Pages 3{EN}7 were read {EN} then dropped.")
    assert ("FAIL", "ENDASH-RANGE") in hits
    assert ("FAIL", "ENDASH-BREAK") in hits


def test_hyphen_and_plain_text_pass():
    assert pt.dash_hits("Pages 3-7 were read, then dropped.") == []
    assert pt.dash_hits("Pages 3 to 7 were read.") == []


def test_check_line_reports_rule_id_not_text():
    out: list = []
    pt.check_line("README.md", 4, BREAKS[3], True, out)
    assert ("FAIL", "README.md:4", "EMDASH-BREAK") in out


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_diff_mode_skips_fences_and_frontmatter_but_not_prose(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("# T\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "work")
    (repo / "README.md").write_text(
        "# T\n\n```\n"
        f"quoted {EM} inside a fence\n"
        "```\n\n"
        f"- term {EM} a definition\n"
        f"The gate blocks it {EM} and logs it.\n",
        encoding="utf-8",
    )
    _git(repo, "commit", "-qam", "change")
    cwd = os.getcwd()
    try:
        os.chdir(repo)
        out = pt.mode_diff("main", "HEAD")
    finally:
        os.chdir(cwd)
    dash = [(w, r) for s, w, r in out if r.startswith("EMDASH")]
    assert dash == [("README.md:8", "EMDASH-BREAK")]


# Alignment: the vendored copy must decide like the repo's voice_lint.py.
VOICE_LINT = os.environ.get(
    "VOICE_LINT_PATH",
    str(HERE / "voice_lint.py"),
)



@pytest.mark.parametrize("line", BREAKS + FORMATTING)
def test_vendored_logic_matches_voice_lint(line):
    spec = importlib.util.spec_from_file_location("voice_lint_ref", VOICE_LINT)
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    assert pt.dash_is_formatting(line, EM) == ref._emdash_is_formatting(line)
