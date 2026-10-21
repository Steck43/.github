#!/usr/bin/env python3
"""voice_lint.py: deterministic anti-slop / clone-fidelity pattern floor.

Derived from aegis-corner skill egress-voice. This is NOT a claim that Landen
authored the text. It fails closed on known AI/slop tells in commits and
public prose so agent dumps do not pass required CI.

Exit 0 = clean. Exit 2 = hit(s) or measurement failure.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

EXIT_FAIL = 2

# --- "underscore": the word is AI vocabulary unless it names the character ----
#
# Every form (underscore, underscores, underscored, underscoring) is flagged in
# any use. Only the singular or plural noun passes, and only in these shapes:
#   - right after a word that cannot start a subject: a determiner or
#     possessive (the, a, an, no, any, every, its, their), a position or
#     count adjective (leading, trailing, double, single, lone, literal), or
#     a preposition (on, by, with, in, of, between), but not before or after;
#   - right after the base, past or -ing form of a verb that takes it as an
#     object (use, allowed, replacing), after "first" or "last" following a
#     determiner ("the first underscore"), or after "before" or "after", only
#     when no object follows: a determiner, possessive or pronoun object after
#     the word (the, a, this, its, them, it), or "that" followed by neither a
#     verb nor its own subject (we, you, they, users, people and the like),
#     makes it a verb ("we added underscore the gap", "this first underscores
#     it", "the run before underscores the risk"); the text is read after
#     NFKC with format and invisible characters dropped, so a fullwidth or
#     invisible-character spelling is the same word;
#   - right after a word that can start a subject, only when number agrees
#     with the noun and no object follows: a singular one (this, each, one)
#     with "underscore", a plural one (these, those, both, many, some, all, a
#     number) with "underscores", or a verb form that is also a plural noun
#     (uses, matches, holds) with "underscores";
#   - right after "that", or a bare "first" or "last", only when a plain noun
#     ending follows: the end of the sentence, is, are, was or were, the
#     glyph in brackets, or another character name in a list;
#   - joined to another word by a hyphen ("underscore-prefixed",
#     "double-underscore");
#   - at the start of a sentence, when what follows is is, are, was, were, a
#     preposition, the end of the sentence, or the rest of a list of character
#     names, or, for the plural only, "that" (a plural cannot be an imperative,
#     so "Underscores that matter" is the noun before a relative clause);
#   - in a list after another character name ("a dash, underscore or dot")
#     when what follows ends the list, names another character, or is one of
#     a few subject verbs (splits, separates, marks, is, are);
#   - right before the glyph in brackets ("underscore (_)") or "as the
#     character";
#   - quoted, when the closing quote is followed by punctuation, the end of the
#     text, or one of is, was, as, means, names, wherever.
# Anything else fails, so a noun in another shape is flagged too (fails safe).
_NAMES = (
    r"(?:dash|hyphen|dot|period|slash|backslash|space|colon|semicolon|comma|"
    r"letter|digit|number|plus|tilde|asterisk)(?:e?s)?"
)
_OBJECT_VERBS = (
    r"allow|use|replace|strip|drop|keep|add|remove|contain|hold|accept|"
    r"reject|permit|forbid|escape|match|insert|join|split"
)
_STRICT = (
    r"the|an?|no|any|every|its|their|our|your|my|double|triple|leading|"
    r"trailing|extra|literal|final|lone|single|escaped|on|by|with|without|in|"
    r"into|from|for|between|than|like|of|about|at|around|inside|per|via"
)
_LEFT_STRICT = re.compile(r"\b(?:" + _STRICT + r")\s+$", re.I)
_LEFT_VERB = re.compile(r"\b(?:" + _OBJECT_VERBS + r")(?:e?d|ing)?\s+$", re.I)
_LEFT_ORDINAL = re.compile(
    r"\b(?:the|its|their|a|each|every|this|that)\s+(?:first|last)\s+$", re.I
)
_LEFT_ONE = re.compile(r"\b(?:this|each|one|another)\s+$", re.I)
_LEFT_MANY = re.compile(
    r"\b(?:these|those|both|many|several|some|few|fewer|more|multiple|all|"
    r"two|three|four|five|(?:" + _OBJECT_VERBS + r")e?s)\s+$",
    re.I,
)
_LEFT_BARE = re.compile(r"\b(?:that|first|last)\s+$", re.I)
# before and after can end a clause as adverbs ("the run before"), so after
# them the word is a noun only when no object follows.
_LEFT_ADVERB = re.compile(r"\b(?:before|after)\s+$", re.I)
# An object after the word: a determiner, possessive or object pronoun, "it",
# or "that" unless a verb follows it (a relative clause: "underscores that
# were missing" names the character).
_OBJECT_AFTER = re.compile(
    r"\s+(?:the|an?|how|why|what|this|these|those|its|our|their|his|her|them|"
    r"us|your|my|it)\b",
    re.I,
)
_THAT_AFTER = re.compile(r"\s+that\b", re.I)
_RELATIVE_THAT = re.compile(
    r"\s+that\s+(?:is|are|was|were|has|have|had|can|could|may|might|must|"
    r"will|would|shall|should|do|does|did|appears?|remains?|stays?|follows?|"
    r"precedes?|separates?|marks?|joins?|splits?|sits?|comes?|starts?|ends?|"
    r"matters?|counts?|survives?|exists?|occurs?|repeats?|belongs?)\b",
    re.I,
)


# "that" followed by its own subject starts a relative clause too
# ("underscores that users type" names the character).
_RELATIVE_SUBJECT = re.compile(
    r"\s+that\s+(?:i|we|you|they|he|she|people|users|readers|writers|authors|"
    r"someone|nobody|everyone|developers|editors|tools|scripts)\b",
    re.I,
)


def _object_follows(after: str) -> bool:
    return bool(
        _OBJECT_AFTER.match(after)
        or (
            _THAT_AFTER.match(after)
            and not _RELATIVE_THAT.match(after)
            and not _RELATIVE_SUBJECT.match(after)
        )
    )


_NOUN_END = re.compile(
    r"\s*(?:$|[.;:!?)])|\s+(?:is|are|was|were)\b|\s*\(\s*(?:_|`_`|\"_\"|'_')?\s*\)"
    r"|\s*(?:,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)(?:an?\s+|the\s+)?"
    + _NAMES
    + r"\b",
    re.I,
)
_SENTENCE_NEXT = re.compile(
    r"\s+(?:in|inside|within|with|between|from|at|on|of|for|before|after)\b",
    re.I,
)
_LEFT_LIST = re.compile(
    r"\b" + _NAMES + r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)$", re.I
)
_SENTENCE_START = re.compile(r"(?:^|[.!?:;(\n])\s*$")
_LIST_END = re.compile(
    r"(?:\s*(?:$|[.;:!?)])|\s*(?:,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
    r"(?:an?\s+|the\s+)?" + _NAMES + r"\b|\s*,?\s+(?:split|separate|mark|join)s?\b|"
    r"\s*,?\s+(?:is|are|was|were|may|can|must)\b)",
    re.I,
)
_LIST_NEXT = re.compile(
    r"\s*(?:,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)(?:an?\s+|the\s+)?"
    + _NAMES
    + r"\b",
    re.I,
)
_GLYPH_OR_AS = re.compile(
    r"\s*\(\s*(?:_|`_`|\"_\"|'_')?\s*\)|\s+as\s+the\s+(?:_\s+)?character\b", re.I
)
_QUOTED_END = re.compile(
    r"\s*(?:$|[.,;:!?)]|(?:is|was|as|means|names|wherever)\b)", re.I
)
_UNDERSCORE = re.compile(r"\bunderscor(?:e|es|ed|ing)\b", re.I)


class _UnderscoreVocab:
    """A pattern stand-in: finditer yields every form of the word except the
    noun in one of the shapes listed above, so every verb use is a hit. A word
    on the left that can start a subject never exempts the word by itself:
    number agreement and what follows decide. After a verb, a determiner with
    an ordinal, or before or after, an object following the word (including
    it, and that when neither a verb nor its own subject follows it) makes it
    a verb."""

    def finditer(self, text: str):
        for m in _UNDERSCORE.finditer(text):
            if not self._names_the_character(text, m):
                yield m

    @staticmethod
    def _names_the_character(text: str, m: re.Match[str]) -> bool:
        word = m.group(0).lower()
        if word not in ("underscore", "underscores"):
            return False
        plural = word == "underscores"
        before = text[max(0, m.start() - 60) : m.start()]
        after = text[m.end() : m.end() + 60]
        for quote in ('"', "'"):
            if before.endswith(quote) and after.startswith(quote):
                return bool(_QUOTED_END.match(after[1:]))
        if before.endswith("-") or re.match(r"-\w", after):
            return True
        if _GLYPH_OR_AS.match(after):
            return True
        if _LEFT_ORDINAL.search(before):
            return not _object_follows(after)
        if _LEFT_BARE.search(before):
            return bool(_NOUN_END.match(after))
        if _LEFT_ONE.search(before):
            return not plural and not _object_follows(after)
        if _LEFT_MANY.search(before):
            return plural and not _object_follows(after)
        if _LEFT_STRICT.search(before):
            return True
        if _LEFT_ADVERB.search(before):
            return not _object_follows(after)
        if _LEFT_VERB.search(before):
            return not _object_follows(after)
        if _LEFT_LIST.search(before):
            return bool(_LIST_END.match(after))
        if _SENTENCE_START.search(text[: m.start()]):
            return bool(
                _LIST_NEXT.match(after)
                or _NOUN_END.match(after)
                or _SENTENCE_NEXT.match(after)
                # A plural cannot be an imperative, so "Underscores that ..."
                # opening a sentence is the noun before a relative clause.
                or (plural and _THAT_AFTER.match(after))
            )
        return False


# (rule_id, severity, pattern, what, fix); patterns from egress-voice bars.
RULES: list[tuple[str, str, re.Pattern[str] | _UnderscoreVocab, str, str]] = [
    (
        "AI-VOCAB",
        "BLOCK",
        re.compile(
            r"\b(additionally|crucial|delve|enduring|enhance|fostering|garner|"
            r"interplay|intricate|pivotal|showcase|tapestry|testament|"
            r"vibrant|landscape)\b",
            re.I,
        ),
        "AI vocabulary / abstract landscape metaphor",
        "Use a plain concrete word",
    ),
    (
        # Every form of "underscore" is AI vocabulary in any use, except the
        # noun that names the _ character. See _UnderscoreVocab.
        "AI-VOCAB",
        "BLOCK",
        _UnderscoreVocab(),
        "AI vocabulary / abstract landscape metaphor",
        "Use a plain concrete word",
    ),
    (
        "SIGNIFICANCE",
        "BLOCK",
        re.compile(
            r"pivotal moment|testament to|evolving landscape|setting the stage|"
            r"indelible mark|deeply rooted|groundbreaking|seamlessly",
            re.I,
        ),
        "Significance inflation / promotional sludge",
        "State what happened without puffery",
    ),
    (
        "CHATBOT",
        "BLOCK",
        re.compile(
            r"I hope this helps|Let me know if|Of course!|Certainly!|"
            r"Great question|You're absolutely right|Found the smoking gun",
            re.I,
        ),
        "Chatbot / sycophantic phrase",
        "Delete; speak as the operator",
    ),
    (
        "FILLER",
        "WARN",
        re.compile(
            r"\bIn order to\b|\bDue to the fact that\b|"
            r"\bIt is important to note that\b|\bcould potentially\b",
            re.I,
        ),
        "Filler / hedge stack",
        "Tighten: To / Because / delete / may",
    ),
    (
        "AI-TRAILER",
        "BLOCK",
        re.compile(
            r"co-authored-by:.*(claude|cursor|copilot|gpt|gemini|assistant)|"
            r"generated with .*(claude|cursor|copilot)",
            re.I,
        ),
        "AI authorship trailer",
        "Author line is Landen Stecker only",
    ),
    (
        "NOT-JUST",
        "WARN",
        re.compile(r"it's not just .{1,40}, it's ", re.I),
        "Negative parallelism template",
        "State the point directly",
    ),
]


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        print(f"voice_lint: measurement failure: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from exc
    if r.returncode not in (0, 1):  # git log empty ranges can be 0
        if r.returncode != 0 and "does not have any commits" not in (r.stderr or ""):
            print(
                f"voice_lint: {' '.join(cmd)} exited {r.returncode}: {r.stderr}",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_FAIL)
    return r.stdout or ""


# --- EMDASH: position matters, the character does not -------------------------
#
# Landen's ruling, 2026-08-18: an em dash used for FORMATTING is fine; an em
# dash used as a GRAMMATICAL BREAK is the tell. The original rule was
# re.compile("\u2014") - a bare character match with no context, which is why it
# produced 114 of 114 hits on isolation-layer-review while the rules aimed at
# actual slop (AI vocab, chatbot phrasing, trailers) found nothing at all. A
# detector whose loudest rule is also its least meaningful one gets removed.
#
# A formatting dash follows a STRUCTURAL MARKER: a heading, a table cell, a list
# label, or a bold/link/code label. It separates a label from its gloss.
# A grammatical dash sits in a plain sentence and joins clauses. Shortness alone
# is NOT the discriminator - "Ship the floor \u2014 then unlock egress." has a short
# left side and is exactly the thing being caught.
EMDASH = "\u2014"
_HEADING = re.compile(r"^\s*#{1,6}\s")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_FM_KEY = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_-]*:\s")
_MARKUP_LABEL = re.compile(
    r"^\s*(?:\*\*.+?\*\*|__.+?__|`.+?`|\[.+?\]\([^)]*\))\s*" + EMDASH
)
_LABEL_MAX = 60


def _strip_label_spans(line: str) -> str:
    """Remove emphasis and code spans - a dash inside one is part of a label.

    `**Stage 0 - Ingestion (Q0).**` names a stage; the dash separates the
    number from the name. That is the same construct as a heading, just inline.
    A dash that survives this strip is sitting in open prose.
    """
    line = re.sub(r"\*\*.+?\*\*", "", line)
    line = re.sub(r"__.+?__", "", line)
    line = re.sub(r"`[^`]*`", "", line)
    return line


def _emdash_is_formatting(line: str) -> bool:
    """True when the em dash on this line is structural, not grammatical."""
    if EMDASH not in _strip_label_spans(line):
        return True
    if _HEADING.match(line):
        return True
    if "|" in line:  # table row
        return True
    if _FM_KEY.match(line):  # frontmatter / key: value
        return True
    if _MARKUP_LABEL.match(line):  # **Label** - gloss
        return True
    m = _LIST_MARK.match(line)
    if m:
        label = line[m.end() :].split(EMDASH, 1)[0].strip()
        # Strip link targets and code spans first. A filename inside a link -
        # `[OPEN NOW](open.md)` - carries a dot that is not sentence punctuation,
        # and reading it as one misfiles an index line as prose.
        label = re.sub(r"\]\([^)]*\)", "]", label)
        label = re.sub(r"`[^`]*`", "", label)
        # A list LABEL is a short noun phrase. A list item that has run on into
        # full sentences before reaching the dash is prose wearing a bullet.
        if len(label) <= _LABEL_MAX and not re.search(r"[.!?]", label):
            return True
    return False


def scan_emdash(label: str, text: str) -> list[str]:
    hits: list[str] = []
    in_fence = False
    in_frontmatter = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if i == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped in ("---", "..."):
                in_frontmatter = False
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or EMDASH not in line:
            continue
        if _emdash_is_formatting(line):
            continue
        hits.append(
            f"{label}:{i}: BLOCK EMDASH: Em dash as a grammatical break. "
            f"saw {line.strip()[:70]!r}. "
            "Fix: use a period, comma, or colon. Formatting dashes "
            "(headings, tables, list labels) are fine."
        )
    return hits


def _blank(line: str) -> str:
    """Same length, same newlines, no words. Offsets must survive masking so a
    hit still reports the real line number and the real snippet."""
    return re.sub(r"[^\n]", " ", line)


def mask_non_prose(text: str) -> str:
    """Blank the spans that quote rather than assert.

    Found 2026-08-19: the pack's own `SENIOR-CRAFT-FLOOR.md` fails this linter.
    Its negative-control section tells you to plant "Additionally, this pivotal
    landscape" in a README to prove `craft (voice)` goes red, and the linter
    read the instruction as the offence. A rule that cannot survive being
    documented gets the documentation weakened instead of the rule.

    scan_emdash already skipped frontmatter and fenced blocks. The RULES loop
    did not, so the two halves of one linter disagreed about what counted as
    prose. This is that skip, shared, plus inline code spans.

    Note the deliberate hole: slop inside backticks is not flagged. A code span
    is an explicit quotation, which is exactly the distinction being drawn, and
    fenced blocks were already exempt on the same reasoning.
    """
    out: list[str] = []
    in_fence = False
    in_frontmatter = False
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.strip()
        if i == 1 and stripped == "---":
            in_frontmatter = True
            out.append(_blank(line))
            continue
        if in_frontmatter:
            if stripped in ("---", "..."):
                in_frontmatter = False
            out.append(_blank(line))
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(_blank(line))
            continue
        if in_fence:
            out.append(_blank(line))
            continue
        out.append(re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), line))
    return "".join(out)


# Code points that render as nothing, folded out before matching so a word
# with one inside is still read as the word (format characters, category Cf,
# are dropped as well).
_INVISIBLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xE0100, 0xE01EF),
)


# Look-alike letters, folded before matching. _UTS39_PACK is derived from
# Unicode's confusables.txt (UTS #39) 18.0.0, dated 2026-08-06, (c) Unicode,
# Inc., under the Unicode License v3 (https://www.unicode.org/license.txt):
# every non-ASCII code point whose skeleton is one ASCII letter or digit, as
# HEX:LETTER pairs. Rebuild it with scripts/gen_confusables.py. The scanner and
# the voice lint each carry this same block (a test keeps the copies equal),
# because CI copies each of them out of the base branch as a single file.
_UTS39_PACK = (
    "A1:i D7:x FE:p 131:i 17F:f 184:b 18D:g 192:f 196:l 1A6:R 1A7:2 1B7:3 1BC:5 "
    "1BD:s 1BF:p 1C0:l 21C:3 222:8 223:8 237:j 24C:R 251:a 261:g 263:y 269:i "
    "26A:i 26F:w 284:f 28B:u 28F:y 37F:J 391:A 392:B 395:E 396:Z 397:H 399:l "
    "39A:K 39C:M 39D:N 39F:O 3A1:P 3A4:T 3A5:Y 3A7:X 3B1:a 3B3:y 3B9:i 3BD:v "
    "3BF:o 3C1:p 3C3:o 3C5:u 3D2:Y 3DC:F 3E8:2 3EC:6 3ED:o 3F1:p 3F2:c 3F3:j "
    "3F8:p 3F9:C 3FA:M 405:S 406:l 408:J 410:A 412:B 415:E 417:3 41A:K 41C:M "
    "41D:H 41E:O 420:P 421:C 422:T 423:Y 425:X 42C:b 430:a 431:6 433:r 435:e "
    "43E:o 440:p 441:c 443:y 445:x 448:w 455:s 456:i 458:j 461:w 474:V 475:v "
    "4AE:Y 4AF:y 4BA:h 4BB:h 4BD:e 4C0:l 4CF:l 4E0:3 501:d 50C:G 51A:Q 51B:q "
    "51C:W 51D:w 545:3 54D:U 54F:S 555:O 561:w 563:q 566:q 570:h 575:j 578:n "
    "57C:n 57D:u 581:g 582:i 584:f 585:o 5C0:l 5D5:l 5D8:v 5DF:l 5E1:o 627:l "
    "647:o 661:l 665:o 667:V 6BE:o 6C1:o 6D5:o 6F1:l 6F5:o 6F7:V 7C0:O 7CA:l "
    "7CB:o 7CC:Y 7D3:F 7D5:b 7E0:T 840:o 964:l 966:o 969:3 9E6:o 9EA:8 9ED:9 "
    "A66:o A67:9 A6A:8 AE6:o AE9:3 B03:8 B20:O B66:o B68:9 BE6:o C02:o C66:o "
    "C82:o CE6:O D02:o D1F:s D20:o D66:o D6D:9 D82:o E50:o ED0:o 1004:c 101D:o "
    "1040:o 104A:l 105A:c 10B9:h 10BD:S 10C7:2 10CD:Z 10E7:y 10FD:S 10FF:o 110B:o "
    "11BC:o 1200:U 12D0:O 13A0:D 13A1:R 13A2:T 13A5:i 13A9:Y 13AA:A 13AB:J 13AC:E "
    "13B3:W 13B7:M 13BB:H 13BD:Y 13C0:G 13C2:h 13C3:Z 13CE:4 13CF:b 13D2:R 13D4:W "
    "13D5:S 13D9:V 13DA:S 13DE:L 13DF:C 13E2:P 13E6:K 13E7:d 13EE:6 13F3:G 13F4:B "
    "142F:V 144C:U 146D:P 146F:d 1472:b 148D:J 14AA:L 14BF:2 1541:x 157C:H 157D:x "
    "1587:R 15AF:b 15B4:F 15C5:A 15DE:D 15EA:D 15F0:M 15F7:B 1616:2 166D:X 166E:x "
    "16B7:X 16C1:l 16D0:l 16D5:K 16D6:M 1702:3 172A:7 1763:x 17E0:o 1A45:o 1A80:o "
    "1A90:o 1BEA:o 1BEC:x 1C82:o 1C83:c 1C84:7 1C95:3 1CB7:2 1CBD:S 1CBF:O 1CF5:X "
    "1D04:c 1D0F:o 1D11:o 1D1C:u 1D20:v 1D21:w 1D22:z 1D26:r 1D83:g 1D8C:y 1E9D:f "
    "1EFF:y 2102:C 210A:g 210B:H 210C:H 210D:H 210E:h 2110:l 2111:l 2112:L 2113:l "
    "2115:N 2119:P 211A:Q 211B:R 211C:R 211D:R 2124:Z 2128:Z 212C:B 212D:C 212E:e "
    "212F:e 2130:E 2131:F 2133:M 2134:o 2139:i 213D:y 2145:D 2146:d 2147:e 2148:i "
    "2149:j 2160:l 2164:V 2169:X 216C:L 216D:C 216E:D 216F:M 2170:i 2174:v 2179:x "
    "217C:l 217D:c 217E:d 21BF:l 2223:l 2228:v 222A:U 22A4:T 22C1:v 22C3:U 22FF:E "
    "2373:i 2374:p 237A:a 23FD:l 2502:l 2503:l 2573:X 27D9:T 292B:x 292C:x 29E2:w "
    "2A2F:x 2C6B:Z 2C6C:z 2C82:B 2C85:r 2C8C:Z 2C8D:z 2C8E:H 2C92:l 2C93:i 2C94:K "
    "2C98:M 2C9A:N 2C9C:3 2C9E:O 2C9F:o 2CA2:P 2CA3:p 2CA4:C 2CA5:c 2CA6:T 2CA8:Y "
    "2CA9:y 2CAC:X 2CBD:w 2CC4:3 2CCA:9 2CCB:9 2CCC:3 2CCE:P 2CCF:p 2CD0:L 2CD2:6 "
    "2CD3:6 2CDC:6 2D2D:z 2D38:V 2D39:E 2D4A:l 2D4F:l 2D54:O 2D55:Q 2D5D:X 3007:O "
    "3112:T 311A:Y 3147:o 4E05:T 4E2B:Y A4D0:B A4D1:P A4D2:d A4D3:D A4D4:T A4D6:G "
    "A4D7:K A4D9:J A4DA:C A4DC:Z A4DD:F A4DF:M A4E0:N A4E1:L A4E2:S A4E3:R A4E6:V "
    "A4E7:H A4EA:W A4EB:X A4EC:Y A4EE:A A4F0:E A4F2:l A4F3:O A4F4:U A50B:T A543:6 "
    "A557:B A56F:l A576:S A589:8 A5CB:E A644:2 A647:i A6C9:Z A6DF:V A6EF:2 A731:s "
    "A75A:2 A76A:3 A76E:9 A76F:9 A781:l A798:F A799:f A79F:u A7AB:3 A7AE:l A7B2:J "
    "A7B3:X A7B4:B A7FA:w A7FE:l A830:l A8CE:l AA5D:l AB32:e AB35:f AB3D:o AB47:r "
    "AB48:r AB4E:u AB52:u AB5A:y AB64:a AB75:i AB81:r AB83:w AB93:z AB9E:4 ABA4:w "
    "ABA9:v ABAA:s ABAF:c ABBE:6 FBA6:o FBA7:o FBA8:o FBA9:o FBAA:o FBAB:o FBAC:o "
    "FBAD:o FE31:l FE8D:l FE8E:l FEE9:o FEEA:o FEEB:o FEEC:o FF10:O FF11:l FF12:2 "
    "FF13:3 FF14:4 FF15:5 FF16:6 FF17:7 FF18:8 FF19:9 FF21:A FF22:B FF23:C FF24:D "
    "FF25:E FF26:F FF27:G FF28:H FF29:l FF2A:J FF2B:K FF2C:L FF2D:M FF2E:N FF2F:O "
    "FF30:P FF31:Q FF32:R FF33:S FF34:T FF35:U FF36:V FF37:W FF38:X FF39:Y FF3A:Z "
    "FF41:a FF42:b FF43:c FF44:d FF45:e FF46:f FF47:g FF48:h FF49:i FF4A:j FF4B:k "
    "FF4C:l FF4E:n FF4F:o FF50:p FF51:q FF52:r FF53:s FF54:t FF55:u FF56:v FF57:w "
    "FF58:x FF59:y FF5A:z FF5C:l FFB7:o FFE8:l 1017E:f 1018B:d 10282:B 10286:E "
    "10287:F 1028A:l 10290:X 10292:O 10295:P 10296:S 10297:T 102A0:A 102A1:B "
    "102A2:C 102A5:F 102AB:O 102B0:M 102B1:T 102B2:Y 102B4:X 102CF:H 102F5:Z "
    "10301:B 10302:C 10309:l 1030F:O 10311:M 10315:T 10317:X 1031A:8 1031C:b "
    "10320:l 10322:X 10404:O 10415:C 1041B:L 10420:S 1042C:o 1043D:c 10448:s "
    "104B4:R 104C2:O 104CE:U 104D2:7 104EA:o 104F6:u 10507:Z 1050E:l 10513:N "
    "10516:O 10518:K 1051B:C 1051D:V 10525:F 10526:L 10527:X 10926:l 1092C:o "
    "10C13:X 10C17:O 10C1F:V 10C20:Y 10C21:M 10C3E:l 10C82:X 10CA5:l 10CC2:x "
    "10CFA:l 10CFC:X 10D07:o 11047:l 110C0:l 11124:o 11141:l 111C5:l 11302:o "
    "113D4:l 11445:8 1144B:l 114D0:o 115C5:l 11641:l 11706:v 1170A:w 1170E:w "
    "1170F:w 118A0:V 118A2:F 118A3:L 118A4:Y 118A6:E 118A9:Z 118AC:9 118AE:E "
    "118AF:4 118B2:L 118B5:O 118B8:U 118BB:5 118BC:T 118C0:v 118C1:s 118C2:F "
    "118C3:i 118C4:y 118C6:7 118C8:o 118CA:3 118CC:9 118D5:6 118D6:9 118D7:o "
    "118D8:u 118DC:y 118E0:O 118E5:Z 118E6:W 118E9:C 118EC:X 118EF:W 118F2:C "
    "11ABC:Z 11ABE:N 11C41:l 11DDA:l 11DE0:O 11DE1:l 169C1:9 169FE:8 16A19:r "
    "16AD6:S 16AE9:O 16D63:l 16E82:4 16E8A:7 16EAA:l 16EB6:b 16F08:V 16F0A:T "
    "16F16:L 16F28:l 16F35:R 16F3A:S 16F3B:3 16F40:A 16F42:U 16F43:Y 1CCD6:A "
    "1CCD7:B 1CCD8:C 1CCD9:D 1CCDA:E 1CCDB:F 1CCDC:G 1CCDD:H 1CCDE:l 1CCDF:J "
    "1CCE0:K 1CCE1:L 1CCE2:M 1CCE3:N 1CCE4:O 1CCE5:P 1CCE6:Q 1CCE7:R 1CCE8:S "
    "1CCE9:T 1CCEA:U 1CCEB:V 1CCEC:W 1CCED:X 1CCEE:Y 1CCEF:Z 1CCF0:O 1CCF1:l "
    "1CCF2:2 1CCF3:3 1CCF4:4 1CCF5:5 1CCF6:6 1CCF7:7 1CCF8:8 1CCF9:9 1CEFC:V "
    "1D100:l 1D134:c 1D1FE:7 1D206:3 1D207:b 1D20C:W 1D20D:V 1D212:7 1D213:F "
    "1D216:R 1D22A:L 1D373:T 1D377:l 1D400:A 1D401:B 1D402:C 1D403:D 1D404:E "
    "1D405:F 1D406:G 1D407:H 1D408:l 1D409:J 1D40A:K 1D40B:L 1D40C:M 1D40D:N "
    "1D40E:O 1D40F:P 1D410:Q 1D411:R 1D412:S 1D413:T 1D414:U 1D415:V 1D416:W "
    "1D417:X 1D418:Y 1D419:Z 1D41A:a 1D41B:b 1D41C:c 1D41D:d 1D41E:e 1D41F:f "
    "1D420:g 1D421:h 1D422:i 1D423:j 1D424:k 1D425:l 1D427:n 1D428:o 1D429:p "
    "1D42A:q 1D42B:r 1D42C:s 1D42D:t 1D42E:u 1D42F:v 1D430:w 1D431:x 1D432:y "
    "1D433:z 1D434:A 1D435:B 1D436:C 1D437:D 1D438:E 1D439:F 1D43A:G 1D43B:H "
    "1D43C:l 1D43D:J 1D43E:K 1D43F:L 1D440:M 1D441:N 1D442:O 1D443:P 1D444:Q "
    "1D445:R 1D446:S 1D447:T 1D448:U 1D449:V 1D44A:W 1D44B:X 1D44C:Y 1D44D:Z "
    "1D44E:a 1D44F:b 1D450:c 1D451:d 1D452:e 1D453:f 1D454:g 1D456:i 1D457:j "
    "1D458:k 1D459:l 1D45B:n 1D45C:o 1D45D:p 1D45E:q 1D45F:r 1D460:s 1D461:t "
    "1D462:u 1D463:v 1D464:w 1D465:x 1D466:y 1D467:z 1D468:A 1D469:B 1D46A:C "
    "1D46B:D 1D46C:E 1D46D:F 1D46E:G 1D46F:H 1D470:l 1D471:J 1D472:K 1D473:L "
    "1D474:M 1D475:N 1D476:O 1D477:P 1D478:Q 1D479:R 1D47A:S 1D47B:T 1D47C:U "
    "1D47D:V 1D47E:W 1D47F:X 1D480:Y 1D481:Z 1D482:a 1D483:b 1D484:c 1D485:d "
    "1D486:e 1D487:f 1D488:g 1D489:h 1D48A:i 1D48B:j 1D48C:k 1D48D:l 1D48F:n "
    "1D490:o 1D491:p 1D492:q 1D493:r 1D494:s 1D495:t 1D496:u 1D497:v 1D498:w "
    "1D499:x 1D49A:y 1D49B:z 1D49C:A 1D49E:C 1D49F:D 1D4A2:G 1D4A5:J 1D4A6:K "
    "1D4A9:N 1D4AA:O 1D4AB:P 1D4AC:Q 1D4AE:S 1D4AF:T 1D4B0:U 1D4B1:V 1D4B2:W "
    "1D4B3:X 1D4B4:Y 1D4B5:Z 1D4B6:a 1D4B7:b 1D4B8:c 1D4B9:d 1D4BB:f 1D4BD:h "
    "1D4BE:i 1D4BF:j 1D4C0:k 1D4C1:l 1D4C3:n 1D4C5:p 1D4C6:q 1D4C7:r 1D4C8:s "
    "1D4C9:t 1D4CA:u 1D4CB:v 1D4CC:w 1D4CD:x 1D4CE:y 1D4CF:z 1D4D0:A 1D4D1:B "
    "1D4D2:C 1D4D3:D 1D4D4:E 1D4D5:F 1D4D6:G 1D4D7:H 1D4D8:l 1D4D9:J 1D4DA:K "
    "1D4DB:L 1D4DC:M 1D4DD:N 1D4DE:O 1D4DF:P 1D4E0:Q 1D4E1:R 1D4E2:S 1D4E3:T "
    "1D4E4:U 1D4E5:V 1D4E6:W 1D4E7:X 1D4E8:Y 1D4E9:Z 1D4EA:a 1D4EB:b 1D4EC:c "
    "1D4ED:d 1D4EE:e 1D4EF:f 1D4F0:g 1D4F1:h 1D4F2:i 1D4F3:j 1D4F4:k 1D4F5:l "
    "1D4F7:n 1D4F8:o 1D4F9:p 1D4FA:q 1D4FB:r 1D4FC:s 1D4FD:t 1D4FE:u 1D4FF:v "
    "1D500:w 1D501:x 1D502:y 1D503:z 1D504:A 1D505:B 1D507:D 1D508:E 1D509:F "
    "1D50A:G 1D50D:J 1D50E:K 1D50F:L 1D510:M 1D511:N 1D512:O 1D513:P 1D514:Q "
    "1D516:S 1D517:T 1D518:U 1D519:V 1D51A:W 1D51B:X 1D51C:Y 1D51E:a 1D51F:b "
    "1D520:c 1D521:d 1D522:e 1D523:f 1D524:g 1D525:h 1D526:i 1D527:j 1D528:k "
    "1D529:l 1D52B:n 1D52C:o 1D52D:p 1D52E:q 1D52F:r 1D530:s 1D531:t 1D532:u "
    "1D533:v 1D534:w 1D535:x 1D536:y 1D537:z 1D538:A 1D539:B 1D53B:D 1D53C:E "
    "1D53D:F 1D53E:G 1D540:l 1D541:J 1D542:K 1D543:L 1D544:M 1D546:O 1D54A:S "
    "1D54B:T 1D54C:U 1D54D:V 1D54E:W 1D54F:X 1D550:Y 1D552:a 1D553:b 1D554:c "
    "1D555:d 1D556:e 1D557:f 1D558:g 1D559:h 1D55A:i 1D55B:j 1D55C:k 1D55D:l "
    "1D55F:n 1D560:o 1D561:p 1D562:q 1D563:r 1D564:s 1D565:t 1D566:u 1D567:v "
    "1D568:w 1D569:x 1D56A:y 1D56B:z 1D56C:A 1D56D:B 1D56E:C 1D56F:D 1D570:E "
    "1D571:F 1D572:G 1D573:H 1D574:l 1D575:J 1D576:K 1D577:L 1D578:M 1D579:N "
    "1D57A:O 1D57B:P 1D57C:Q 1D57D:R 1D57E:S 1D57F:T 1D580:U 1D581:V 1D582:W "
    "1D583:X 1D584:Y 1D585:Z 1D586:a 1D587:b 1D588:c 1D589:d 1D58A:e 1D58B:f "
    "1D58C:g 1D58D:h 1D58E:i 1D58F:j 1D590:k 1D591:l 1D593:n 1D594:o 1D595:p "
    "1D596:q 1D597:r 1D598:s 1D599:t 1D59A:u 1D59B:v 1D59C:w 1D59D:x 1D59E:y "
    "1D59F:z 1D5A0:A 1D5A1:B 1D5A2:C 1D5A3:D 1D5A4:E 1D5A5:F 1D5A6:G 1D5A7:H "
    "1D5A8:l 1D5A9:J 1D5AA:K 1D5AB:L 1D5AC:M 1D5AD:N 1D5AE:O 1D5AF:P 1D5B0:Q "
    "1D5B1:R 1D5B2:S 1D5B3:T 1D5B4:U 1D5B5:V 1D5B6:W 1D5B7:X 1D5B8:Y 1D5B9:Z "
    "1D5BA:a 1D5BB:b 1D5BC:c 1D5BD:d 1D5BE:e 1D5BF:f 1D5C0:g 1D5C1:h 1D5C2:i "
    "1D5C3:j 1D5C4:k 1D5C5:l 1D5C7:n 1D5C8:o 1D5C9:p 1D5CA:q 1D5CB:r 1D5CC:s "
    "1D5CD:t 1D5CE:u 1D5CF:v 1D5D0:w 1D5D1:x 1D5D2:y 1D5D3:z 1D5D4:A 1D5D5:B "
    "1D5D6:C 1D5D7:D 1D5D8:E 1D5D9:F 1D5DA:G 1D5DB:H 1D5DC:l 1D5DD:J 1D5DE:K "
    "1D5DF:L 1D5E0:M 1D5E1:N 1D5E2:O 1D5E3:P 1D5E4:Q 1D5E5:R 1D5E6:S 1D5E7:T "
    "1D5E8:U 1D5E9:V 1D5EA:W 1D5EB:X 1D5EC:Y 1D5ED:Z 1D5EE:a 1D5EF:b 1D5F0:c "
    "1D5F1:d 1D5F2:e 1D5F3:f 1D5F4:g 1D5F5:h 1D5F6:i 1D5F7:j 1D5F8:k 1D5F9:l "
    "1D5FB:n 1D5FC:o 1D5FD:p 1D5FE:q 1D5FF:r 1D600:s 1D601:t 1D602:u 1D603:v "
    "1D604:w 1D605:x 1D606:y 1D607:z 1D608:A 1D609:B 1D60A:C 1D60B:D 1D60C:E "
    "1D60D:F 1D60E:G 1D60F:H 1D610:l 1D611:J 1D612:K 1D613:L 1D614:M 1D615:N "
    "1D616:O 1D617:P 1D618:Q 1D619:R 1D61A:S 1D61B:T 1D61C:U 1D61D:V 1D61E:W "
    "1D61F:X 1D620:Y 1D621:Z 1D622:a 1D623:b 1D624:c 1D625:d 1D626:e 1D627:f "
    "1D628:g 1D629:h 1D62A:i 1D62B:j 1D62C:k 1D62D:l 1D62F:n 1D630:o 1D631:p "
    "1D632:q 1D633:r 1D634:s 1D635:t 1D636:u 1D637:v 1D638:w 1D639:x 1D63A:y "
    "1D63B:z 1D63C:A 1D63D:B 1D63E:C 1D63F:D 1D640:E 1D641:F 1D642:G 1D643:H "
    "1D644:l 1D645:J 1D646:K 1D647:L 1D648:M 1D649:N 1D64A:O 1D64B:P 1D64C:Q "
    "1D64D:R 1D64E:S 1D64F:T 1D650:U 1D651:V 1D652:W 1D653:X 1D654:Y 1D655:Z "
    "1D656:a 1D657:b 1D658:c 1D659:d 1D65A:e 1D65B:f 1D65C:g 1D65D:h 1D65E:i "
    "1D65F:j 1D660:k 1D661:l 1D663:n 1D664:o 1D665:p 1D666:q 1D667:r 1D668:s "
    "1D669:t 1D66A:u 1D66B:v 1D66C:w 1D66D:x 1D66E:y 1D66F:z 1D670:A 1D671:B "
    "1D672:C 1D673:D 1D674:E 1D675:F 1D676:G 1D677:H 1D678:l 1D679:J 1D67A:K "
    "1D67B:L 1D67C:M 1D67D:N 1D67E:O 1D67F:P 1D680:Q 1D681:R 1D682:S 1D683:T "
    "1D684:U 1D685:V 1D686:W 1D687:X 1D688:Y 1D689:Z 1D68A:a 1D68B:b 1D68C:c "
    "1D68D:d 1D68E:e 1D68F:f 1D690:g 1D691:h 1D692:i 1D693:j 1D694:k 1D695:l "
    "1D697:n 1D698:o 1D699:p 1D69A:q 1D69B:r 1D69C:s 1D69D:t 1D69E:u 1D69F:v "
    "1D6A0:w 1D6A1:x 1D6A2:y 1D6A3:z 1D6A4:i 1D6A5:j 1D6A8:A 1D6A9:B 1D6AC:E "
    "1D6AD:Z 1D6AE:H 1D6B0:l 1D6B1:K 1D6B3:M 1D6B4:N 1D6B6:O 1D6B8:P 1D6BB:T "
    "1D6BC:Y 1D6BE:X 1D6C2:a 1D6C4:y 1D6CA:i 1D6CE:v 1D6D0:o 1D6D2:p 1D6D4:o "
    "1D6D6:u 1D6E0:p 1D6E2:A 1D6E3:B 1D6E6:E 1D6E7:Z 1D6E8:H 1D6EA:l 1D6EB:K "
    "1D6ED:M 1D6EE:N 1D6F0:O 1D6F2:P 1D6F5:T 1D6F6:Y 1D6F8:X 1D6FC:a 1D6FE:y "
    "1D704:i 1D708:v 1D70A:o 1D70C:p 1D70E:o 1D710:u 1D71A:p 1D71C:A 1D71D:B "
    "1D720:E 1D721:Z 1D722:H 1D724:l 1D725:K 1D727:M 1D728:N 1D72A:O 1D72C:P "
    "1D72F:T 1D730:Y 1D732:X 1D736:a 1D738:y 1D73E:i 1D742:v 1D744:o 1D746:p "
    "1D748:o 1D74A:u 1D754:p 1D756:A 1D757:B 1D75A:E 1D75B:Z 1D75C:H 1D75E:l "
    "1D75F:K 1D761:M 1D762:N 1D764:O 1D766:P 1D769:T 1D76A:Y 1D76C:X 1D770:a "
    "1D772:y 1D778:i 1D77C:v 1D77E:o 1D780:p 1D782:o 1D784:u 1D78E:p 1D790:A "
    "1D791:B 1D794:E 1D795:Z 1D796:H 1D798:l 1D799:K 1D79B:M 1D79C:N 1D79E:O "
    "1D7A0:P 1D7A3:T 1D7A4:Y 1D7A6:X 1D7AA:a 1D7AC:y 1D7B2:i 1D7B6:v 1D7B8:o "
    "1D7BA:p 1D7BC:o 1D7BE:u 1D7C8:p 1D7CA:F 1D7CE:O 1D7CF:l 1D7D0:2 1D7D1:3 "
    "1D7D2:4 1D7D3:5 1D7D4:6 1D7D5:7 1D7D6:8 1D7D7:9 1D7D8:O 1D7D9:l 1D7DA:2 "
    "1D7DB:3 1D7DC:4 1D7DD:5 1D7DE:6 1D7DF:7 1D7E0:8 1D7E1:9 1D7E2:O 1D7E3:l "
    "1D7E4:2 1D7E5:3 1D7E6:4 1D7E7:5 1D7E8:6 1D7E9:7 1D7EA:8 1D7EB:9 1D7EC:O "
    "1D7ED:l 1D7EE:2 1D7EF:3 1D7F0:4 1D7F1:5 1D7F2:6 1D7F3:7 1D7F4:8 1D7F5:9 "
    "1D7F6:O 1D7F7:l 1D7F8:2 1D7F9:3 1D7FA:4 1D7FB:5 1D7FC:6 1D7FD:7 1D7FE:8 "
    "1D7FF:9 1DF5A:a 1DF6A:A 1DF7D:w 1DF81:E 1E140:O 1E141:l 1E145:V 1E2F0:O "
    "1E2F2:9 1E8C7:l 1E8CB:8 1ED01:l 1EE00:l 1EE24:o 1EE80:l 1EE84:o 1F74C:C "
    "1F768:T 1FBF0:O 1FBF1:l 1FBF2:2 1FBF3:3 1FBF4:4 1FBF5:5 1FBF6:6 1FBF7:7 "
    "1FBF8:8 1FBF9:9"
)
# Look-alikes the Unicode table leaves out, because their skeleton is not a
# single ASCII letter: Latin small capitals, a few Cyrillic and Greek letters,
# and Latin letters drawn like a plain one with a tail, a bar or a turn.
_LOOKALIKE_EXTRA = (
    "24F:y 262:g 274:n 280:r 299:b 29C:h 29F:l 3B5:e 3BA:k 432:b 43A:k 43C:m "
    "43D:h 442:t 1D00:a 1D05:d 1D07:e 1D0A:j 1D0B:k 1D0D:m 1D18:p 1D1B:t A730:f "
    "27D:r 27E:r 1DD:e 454:e 19A:l 268:i 1D09:i"
)
# Capital-I look-alikes, by hand on top of the Unicode table. Its skeleton for
# ASCII I is lowercase l, so it folds each of these to l, and a name with an i
# written with one still hid. Each folds to I, the ASCII capital it imitates.
# ASCII itself is never folded, so 0, 1 and | stay as written.
_CAPITAL_I = (
    "196:I 1C0:I 399:I 406:I 4C0:I 16C1:I 2C92:I A4F2:I A7AE:I A7FE:I 10309:I 10926:I"
)
_CONFUSABLES = str.maketrans(
    {
        chr(int(code, 16)): letter
        for code, letter in (
            pair.split(":")
            for pair in (
                _UTS39_PACK + " " + _LOOKALIKE_EXTRA + " " + _CAPITAL_I
            ).split()
        )
    }
)


def _fold(text: str) -> str:
    """NFKC (fullwidth and compatibility letters become plain ones), then drop
    format characters and the invisible code points above, then fold look-alike
    letters to Latin. Line feeds stay, so line numbers counted on the folded
    text match the original."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(
        ch
        for ch in text
        if unicodedata.category(ch) != "Cf"
        and not any(lo <= ord(ch) <= hi for lo, hi in _INVISIBLE)
    ).translate(_CONFUSABLES)


def scan_text(label: str, text: str, *, fail_on_warn: bool) -> list[str]:
    hits: list[str] = []
    # Match against masked text so quotations do not read as assertions, then
    # fold it so a fullwidth or invisible-character spelling still matches.
    # Hits are located and quoted from the folded text: folding can change
    # lengths but keeps every line feed.
    masked = _fold(mask_non_prose(text))
    for rid, sev, pat, what, fix in RULES:
        if sev == "WARN" and not fail_on_warn:
            continue
        for m in pat.finditer(masked):
            line_no = masked[: m.start()].count("\n") + 1
            snippet = masked[m.start() : m.end()].replace("\n", " ")[:80]
            hits.append(
                f"{label}:{line_no}: {sev} {rid}: {what}. saw {snippet!r}. Fix: {fix}"
            )
    hits.extend(scan_emdash(label, text))
    return hits


def scan_commits(base: str, head: str, *, fail_on_warn: bool) -> list[str]:
    # Prefer range; fall back to last 20 if base missing.
    out = _run(["git", "log", f"{base}..{head}", "--format=%B"])
    if not out.strip():
        out = _run(["git", "log", "-n", "20", "--format=%B"])
    return scan_text("commit", out, fail_on_warn=fail_on_warn)


def scan_paths(paths: list[Path], *, fail_on_warn: bool) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"voice_lint: cannot read {path}: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_FAIL) from exc
        hits.extend(scan_text(str(path), text, fail_on_warn=fail_on_warn))
    return hits


def default_prose_paths(root: Path) -> list[Path]:
    names = ["CHANGELOG.md", "CHANGELOG", "README.md", "README"]
    found = [root / n for n in names if (root / n).is_file()]
    docs = root / "docs"
    if docs.is_dir():
        found.extend(sorted(docs.rglob("*.md")))
    return found


def selftest() -> int:
    bad = 0
    total = 0

    def expect(label: str, text: str, want_hit: bool) -> None:
        nonlocal bad, total
        total += 1
        hits = scan_text(label, text, fail_on_warn=True)
        got = bool(hits)
        ok = got == want_hit
        print(f"  {'ok' if ok else 'FAIL'} {label} want_hit={want_hit} got={got}")
        if not ok:
            bad += 1
            for h in hits[:3]:
                print(f"    {h}")

    expect("nc-vocab", "Additionally, this pivotal landscape is crucial.", True)
    # Mention, not use. Every one of these carries the same banned words as the
    # line above, which must stay red.
    expect(
        "ok-vocab-in-fence",
        "Plant this:\n\n```\nAdditionally, this pivotal landscape is crucial.\n```\n",
        False,
    )
    expect(
        "ok-vocab-in-code-span",
        "Plant `Additionally, this pivotal landscape` in a README to prove it reds.",
        False,
    )
    expect(
        "ok-vocab-in-frontmatter",
        "---\ntitle: Additionally the pivotal landscape\n---\n\nClean prose here.\n",
        False,
    )
    expect(
        "nc-vocab-after-a-fence-closes",
        "```\ncode\n```\n\nAdditionally, this pivotal landscape is crucial.\n",
        True,
    )
    expect(
        "nc-vocab-outside-backticks",
        "Run `make check`. Additionally, this pivotal landscape is crucial.",
        True,
    )
    expect("nc-chat", "I hope this helps! Let me know if you need anything.", True)
    expect("nc-emdash", "Ship the floor \u2014 then unlock egress.", True)
    # Grammatical breaks: still blocked.
    expect("nc-emdash-paired", "The floor \u2014 not the judge \u2014 decides.", True)
    expect(
        "nc-emdash-longbullet",
        "- The floor is CI on someone else's machine, and it only binds once "
        "it is required \u2014 which is a settings flip.",
        True,
    )
    # Formatting: allowed.
    expect("ok-emdash-heading", "# Artifact Index \u2014 volume 2", False)
    expect("ok-emdash-table", "| 2026-08-18 | path | what \u2014 detail | LIVE |", False)
    expect("ok-emdash-bold", "**Floor** \u2014 deterministic, non-bypassable", False)
    expect("ok-emdash-list", "- gitleaks \u2014 secrets, working tree and history", False)
    expect("ok-emdash-link", "- [OPEN NOW](open.md) \u2014 read this first", False)
    expect("nc-trailer", "Co-Authored-By: Cursor <cursor@example.com>", True)
    expect(
        "clean",
        "Require PR plus floor checks before merge. Author: Landen Stecker.",
        False,
    )
    print(f"\n{total - bad}/{total} selftest cases correct")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--commits", action="store_true", help="scan git commit messages")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--paths", nargs="*", type=Path, help="explicit files to scan")
    ap.add_argument(
        "--prose",
        action="store_true",
        help="scan CHANGELOG/README/docs under cwd",
    )
    ap.add_argument(
        "--fail-on-warn",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="treat WARN rules as failing (default: true for CI)",
    )
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    hits: list[str] = []
    fail_on_warn = bool(args.fail_on_warn)
    if args.commits:
        hits.extend(scan_commits(args.base, args.head, fail_on_warn=fail_on_warn))
    paths = list(args.paths or [])
    if args.prose:
        paths.extend(default_prose_paths(Path.cwd()))
    if paths:
        hits.extend(scan_paths(paths, fail_on_warn=fail_on_warn))
    if not args.commits and not paths:
        print(
            "voice_lint: nothing to scan (pass --commits and/or --prose/--paths)",
            file=sys.stderr,
        )
        return EXIT_FAIL
    for h in hits:
        print(h, file=sys.stderr)
    if hits:
        print(f"voice_lint: {len(hits)} hit(s)", file=sys.stderr)
        return EXIT_FAIL
    print("voice_lint: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
