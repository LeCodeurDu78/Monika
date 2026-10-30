"""Turn streamed Markdown answers into clean sentences that are pleasant to hear."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

CODE_NOTICE = (
    "Je te laisse le code à l'écran."  # Spoken once instead of reading a code block
)
LINK_WORD = "un lien"  # Spoken instead of a bare URL

_CLOSERS = "\"'»)]*_”’"
_FENCE = re.compile(r"^\s{0,3}(?:`{3,}|~{3,})")
_MARKER = re.compile(r"^\s*(?:(?:#{1,6}|[-*+•>]|\d{1,3}[.)])\s+|>)+")
# A line start that may still turn into a list marker or a code fence: wait for more text
_HOLD = re.compile(r"^\s*(?:#{1,6}|[-*+•>]|\d{1,3}[.)]?|`{1,3}|~{1,3})?\s*$")
_SENT_END = re.compile(rf"[.!?…]+[{re.escape(_CLOSERS)}]*(?=\s)")
_END_PUNCT = (".", "!", "?", "…", ":", ";", ",")
_ABBREV = frozenset(
    {
        "m",
        "mm",
        "mme",
        "mlle",
        "mr",
        "dr",
        "pr",
        "st",
        "ste",
        "cf",
        "vs",
        "env",
        "approx",
        "fig",
        "ex",
        "p",
    }
)

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)\s]*\)")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"»”’]")
_PATH = re.compile(r"(?<![\w/:.])(~|\.{1,2})?((?:/[\w.\-@+%]+)+)/?")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# Units are spoken with their name: 32000 Hz is "trente-deux mille hertz", not "Hz". The case is
# significant: "GB" is a gigabyte, "Gb" a gigabit (and "Mo" stays French).
_OCTETS = ("octet", "octets")
_BITS = ("bit", "bits")


def _named(prefix: str, unit: tuple[str, str]) -> tuple[str, str]:
    return (prefix + unit[0], prefix + unit[1])


_UNIT_NAMES: dict[str, tuple[str, str]] = {
    # Capacities: "o" is the French spelling, "B" the English one for the same byte
    "Ko": _named("kilo", _OCTETS),
    "KB": _named("kilo", _OCTETS),
    "KiB": _named("kibi", _OCTETS),
    "Mo": _named("méga", _OCTETS),
    "MB": _named("méga", _OCTETS),
    "MiB": _named("mibi", _OCTETS),
    "Go": _named("giga", _OCTETS),
    "GB": _named("giga", _OCTETS),
    "GiB": _named("gibi", _OCTETS),
    "To": _named("téra", _OCTETS),
    "TB": _named("téra", _OCTETS),
    "TiB": _named("tébi", _OCTETS),
    "Po": _named("péta", _OCTETS),
    "PB": _named("péta", _OCTETS),
    "Eo": _named("exa", _OCTETS),
    "EB": _named("exa", _OCTETS),
    # Bits: the lowercase b is what keeps "1 Gb" a gigabit
    "Kb": _named("kilo", _BITS),
    "Kbit": _named("kilo", _BITS),
    "Mb": _named("méga", _BITS),
    "Mbit": _named("méga", _BITS),
    "Gb": _named("giga", _BITS),
    "Gbit": _named("giga", _BITS),
    "Tb": _named("téra", _BITS),
    "Tbit": _named("téra", _BITS),
    # Frequencies: hertz is invariable in French, so singular and plural are the same word
    "Hz": ("hertz", "hertz"),
    "kHz": ("kilohertz", "kilohertz"),
    "MHz": ("mégahertz", "mégahertz"),
    "GHz": ("gigahertz", "gigahertz"),
    "THz": ("térahertz", "térahertz"),
}

_SYMBOLS = "|".join(sorted(_UNIT_NAMES, key=len, reverse=True))
# A rate first: "10 Mb/s" would otherwise become "10 mégabit/s"
_RATE = re.compile(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*({_SYMBOLS})\s*(?:/\s*s|ps)(?!\w)")
# Only a symbol glued to a number is a unit, so "le Go" and "3 ans de Go" are left alone
_QUANTITY = re.compile(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*({_SYMBOLS})(?!\w)")


def _pick(count: str, names: tuple[str, str]) -> str:
    """French takes the singular below two: "1 gigaoctet", "0,5 gigaoctet", "2 gigaoctets"."""
    try:
        plural = float(count.replace(",", ".")) >= 2
    except ValueError:
        plural = True
    return names[1] if plural else names[0]


def _spoken_units(text: str) -> str:
    """Say "3 Go" as "3 gigaoctets": the name is used only when a number comes right before it."""
    text = _RATE.sub(
        lambda m: (
            f"{m.group(1)} {_pick(m.group(1), _UNIT_NAMES[m.group(2)])} par seconde"
        ),
        text,
    )
    return _QUANTITY.sub(
        lambda m: f"{m.group(1)} {_pick(m.group(1), _UNIT_NAMES[m.group(2)])}", text
    )


def _path_name(match: re.Match[str]) -> str:
    """Keep only the last component of a path; leave short slash words ('/ou') untouched."""
    parts = match.group(2).split("/")[1:]
    if not match.group(1) and len(parts) < 2:
        return match.group(0)
    return parts[-1]


def _time_words(match: re.Match[str]) -> str:
    hours, minutes = match.group(1), match.group(2)
    return f"{hours} h" if minutes == "00" else f"{hours} h {minutes}"


def clean_for_speech(text: str) -> str:
    """Make one line of Markdown speakable; returns '' when nothing worth saying remains."""
    text = text.replace("\r", " ").replace("\n", " ")
    stripped = text.strip()
    if stripped.startswith("|"):  # Table row: cells become a spoken enumeration
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
            return ""
        text = ", ".join(c for c in cells if c)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(LINK_WORD, text)
    text = _PATH.sub(_path_name, text)
    text = _HTML_TAG.sub("", text)
    text = re.sub(r"(\d)\s*%", r"\1 pour cent", text).replace("%", " pour cent ")
    text = re.sub(r"(\d)\s*€", r"\1 euros", text).replace("€", " euros ")
    text = re.sub(r"(\d)\s*\$", r"\1 dollars", text)
    text = re.sub(r"(\d)\s*°(?:\s*[CcFf]\b)?", r"\1 degrés", text)
    text = _TIME.sub(_time_words, text)
    text = _spoken_units(text)
    text = text.replace("&", " et ")
    text = re.sub(r"\s*(?:->|=>|→|⇒)\s*", ", ", text)
    text = text.replace("`", "").replace("*", "").replace("~", "").replace("|", ", ")
    text = re.sub(r"(?<=\w)_(?=\w)", " ", text).replace("_", "")
    text = "".join(c for c in text if unicodedata.category(c) not in ("So", "Sk", "Cf"))
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    return text if any(c.isalnum() for c in text) else ""


def _cut(text: str, limit: int) -> tuple[str, str]:
    """Split text that is too long, preferably after a comma-like pause, else at a space."""
    window = text[:limit]
    best = -1
    for sep in (", ", "; ", ": "):
        i = window.rfind(sep)
        if i > limit // 3:
            best = max(best, i + 1)
    if best > 0:
        return text[:best], text[best:].lstrip()
    i = window.rfind(" ")
    if i > 0:
        return text[:i], text[i + 1 :].lstrip()
    return window, text[limit:]


def _wrap(text: str, limit: int) -> list[str]:
    pieces = []
    while len(text) > limit:
        head, text = _cut(text, limit)
        pieces.append(head)
    return pieces + [text]


def group_blocks(sentences: Iterable[str], max_chars: int) -> list[str]:
    """Join sentences into blocks of at most `max_chars`: one block is one request to the voice.

    A sentence is never split (a block containing a single long sentence simply exceeds the limit),
    and the gaps between sentences are kept as single spaces.
    """
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        text = " ".join(str(sentence).split())
        if not text:
            continue
        if not current:
            current = text
        elif len(current) + 1 + len(text) <= max_chars:
            current = f"{current} {text}"
        else:
            blocks.append(current)
            current = text
    if current:
        blocks.append(current)
    return blocks


def _is_abbreviation(before: str, terminator: str) -> bool:
    """'M. Dupont', 'p. ex.' or an initial like 'J. K.' do not end a sentence."""
    if terminator.rstrip(_CLOSERS) != ".":
        return False
    found = re.search(r"([^\W\d_]+)$", before)
    if not found:
        return False
    word = found.group(1)
    return word.lower() in _ABBREV or (len(word) == 1 and word.isupper())


class SentenceSplitter:
    """Feed it streamed Markdown; it returns clean sentences as soon as they are complete."""

    def __init__(self, min_chars: int = 15, max_chars: int = 200) -> None:
        self._min = min_chars
        self._max = max_chars
        self._buf = ""
        self._mid_line = False  # The start of the current line was already handled
        self._in_code = False
        self._swallow_line = (
            False  # The current line is the opening fence of a code block
        )
        self._carry = ""  # Short sentences wait here and are merged with the next one
        self._out: list[str] = []

    def feed(self, text: str) -> list[str]:
        """Add streamed text; returns the sentences that are now complete."""
        self._buf += text.replace("\r", "")
        self._consume(final=False)
        return self._take()

    def flush(self) -> list[str]:
        """Call at the end of a text run (before a tool call, at the end of the turn)."""
        self._consume(final=True)
        if self._carry:
            self._out.append(self._carry)
        self._carry, self._buf = "", ""
        self._mid_line = self._in_code = self._swallow_line = False
        return self._take()

    def _take(self) -> list[str]:
        out, self._out = self._out, []
        return out

    def _consume(self, final: bool) -> None:
        while True:
            newline = self._buf.find("\n")
            if self._in_code:
                if newline < 0:
                    if final:
                        self._buf = ""
                    return
                line, self._buf = self._buf[:newline], self._buf[newline + 1 :]
                if self._swallow_line:
                    self._swallow_line = False
                elif _FENCE.match(line):
                    self._in_code = False
                continue
            if newline >= 0:
                line, self._buf = self._buf[:newline], self._buf[newline + 1 :]
                self._line(line)
                self._mid_line = False
            elif final and self._buf:
                line, self._buf = self._buf, ""
                self._line(line)
                self._mid_line = False
            elif self._buf:
                self._partial()
                return
            else:
                return

    def _open_code(self) -> None:
        self._in_code = True
        self._push(CODE_NOTICE, hard=True)

    def _line(self, line: str) -> None:
        """Handle the rest of a line that is now complete."""
        if not self._mid_line:
            if _FENCE.match(line):
                self._open_code()
                return
            line = _MARKER.sub("", line, count=1)
        units, rest = self._split(line, final=True)
        for unit in units:
            self._push(unit, hard=False)
        self._push(rest, hard=True)

    def _partial(self) -> None:
        """Handle an unfinished line: emit the sentences already complete, keep the rest."""
        if not self._mid_line:
            if _FENCE.match(self._buf):
                self._open_code()
                self._swallow_line = True
                return
            if _HOLD.match(self._buf):
                return
            self._buf = _MARKER.sub("", self._buf, count=1)
            self._mid_line = True
        units, self._buf = self._split(self._buf, final=False)
        for unit in units:
            self._push(unit, hard=False)

    def _split(self, text: str, final: bool) -> tuple[list[str], str]:
        """Cut complete sentences off the front of text; returns (sentences, remainder)."""
        units: list[str] = []
        while True:
            cut = None
            for match in _SENT_END.finditer(text):
                if not _is_abbreviation(text[: match.start()], match.group()):
                    cut = match.end()
                    break
            if cut is not None:
                units.append(text[:cut])
                text = text[cut:].lstrip()
            elif not final and len(text) > self._max:
                head, text = _cut(text, self._max)
                units.append(head)
            else:
                return units, text

    def _push(self, unit: str, hard: bool) -> None:
        """Clean one unit, wrap it to the length limit and merge it with short neighbours."""
        text = clean_for_speech(unit)
        if not text:
            return
        pieces = _wrap(text, self._max)
        if hard and not pieces[-1].endswith(_END_PUNCT):
            pieces[-1] += (
                "."  # A line break is a pause: make it one for the voice as well
            )
        for piece in pieces:
            self._carry = f"{self._carry} {piece}".strip()
            if len(self._carry) >= self._min:
                self._out.append(self._carry)
                self._carry = ""
