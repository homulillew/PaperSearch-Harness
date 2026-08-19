r"""Mechanical math renderability preflight for report manuscripts.

The invariant: every *supported* mathematical expression in a report manuscript
must be accepted by the configured MathJax renderer before the manuscript can
proceed to Reader certification or publication.

The supported authoring surface is intentionally small and explicit:

    $ ... $        inline math
    $$ ... $$      display math
    \( ... \)      LaTeX inline math
    \[ ... \]      LaTeX display math

Ordinary fenced code blocks and inline code spans are verbatim and are excluded
from extraction. No other Markdown math notation is claimed to be supported.

This module is deliberately *not* a TeX parser and *not* a semantic validator.
It extracts the TeX content of the supported math spans, sends the batch to the
configured MathJax renderer (the locally pinned runtime, via the Node validator
in ``math/validate.js``), and reports which expressions the renderer rejects.
It never rewrites prose and never judges mathematical meaning, style, or
fidelity — only mechanical renderability.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Reuse the exact fence definition the Presentation structural check uses, so
# the two checks agree on what counts as a fenced block.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(?:[^\r\n]*)$")

# An inline code span: a run of backticks opens it, the same number of
# backticks (and no fewer) closes it. Content between is verbatim, so a "$"
# inside `...` is not math.
_INLINE_CODE = re.compile(r"(?<!\\)(`+)(?:.|\n)*?\1")

# Math delimiters. $$ (display) must be matched before $ (inline) so that a
# display span is not misread as two inline spans. \(...\) and \[...\] are
# LaTeX inline/display forms. Escaped delimiters (\$, \(, \[) are not openers.
_DISPLAY_DOLLAR = re.compile(r"(?<!\\)\$\$")
_INLINE_DOLLAR = re.compile(r"(?<!\\)\$")
_PAREN_INLINE = re.compile(r"(?<!\\)\\\(([\s\S]*?)(?<!\\)\\\)")
_BRACKET_DISPLAY = re.compile(r"(?<!\\)\\\[([\s\S]*?)(?<!\\)\\\]")

# The validator lives next to this module, under runtime/math/.
_VALIDATOR = Path(__file__).resolve().parent / "math" / "validate.js"


@dataclass(slots=True, frozen=True)
class MathRejection:
    """A math expression the configured MathJax renderer refused to render."""

    expression: str
    error: str


class MathRendererUnavailable(RuntimeError):
    """The configured MathJax renderer could not be located or run.

    Raised fail-closed: if a manuscript contains math but the renderer cannot
    be exercised, renderability cannot be certified, so the manuscript must not
    proceed.
    """


def _strip_code_and_fences(markdown: str) -> str:
    """Return prose with fenced blocks removed and inline code spans blanked.

    Fenced blocks are dropped line-by-line (matching the structural check's
    fence tracking, including the same close-fence rule). Inline code spans
    are replaced with a placeholder that carries no math delimiters, so a "$"
    inside `...` is never treated as math.
    """

    fence_character: str | None = None
    fence_length = 0
    prose_lines: list[str] = []
    for line in markdown.splitlines():
        if fence_character is not None:
            if re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}"
                rf"{{{fence_length},}}[ \t]*$",
                line,
            ):
                fence_character = None
                fence_length = 0
            continue
        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        prose_lines.append(line)
    if fence_character is not None:
        # An unclosed fence is a structural defect the Presentation check
        # catches first; here we just keep whatever prose we gathered.
        pass

    prose = "\n".join(prose_lines)
    # Blank inline code spans so their contents cannot be mistaken for math.
    prose = _INLINE_CODE.sub("\x00", prose)
    return prose


def _extract_dollar_spans(prose: str) -> list[tuple[int, str]]:
    """Extract the inner TeX from $...$ and $$...$$ spans in prose.

    Display ($$) delimiters are paired first, then inline ($) delimiters over
    the residue, so a display span is never mis-split into two inline spans.
    Escaped delimiters (\\$) are ignored. Each result is ``(start, inner)`` —
    the start position of the opening delimiter — so callers can merge
    results from other delimiter families into true document order.
    """

    found: list[tuple[int, str]] = []

    def _pair(delimiter: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        pos = 0
        while True:
            start = prose.find(delimiter, pos)
            if start == -1:
                break
            end = prose.find(delimiter, start + len(delimiter))
            if end == -1:
                break
            # Skip escaped openers: a backslash immediately before the delimiter.
            if start > 0 and prose[start - 1] == "\\":
                pos = start + len(delimiter)
                continue
            spans.append((start, end))
            pos = end + len(delimiter)
        return spans

    consumed: list[tuple[int, int]] = []  # (start, end) byte ranges already taken

    display_spans = _pair("$$")
    for start, end in display_spans:
        inner_start = start + 2
        inner_end = end
        found.append((start, prose[inner_start:inner_end].strip()))
        consumed.append((start, end + 2))

    # Inline $ spans, skipping any range overlapped by a display span.
    pos = 0
    while True:
        idx = _INLINE_DOLLAR.search(prose, pos)
        if idx is None:
            break
        start = idx.start()
        # Skip escaped openers.
        if start > 0 and prose[start - 1] == "\\":
            pos = start + 1
            continue
        # Skip if inside a consumed display span.
        if any(s <= start < e for s, e in consumed):
            pos = start + 1
            continue
        end_idx = _INLINE_DOLLAR.search(prose, start + 1)
        if end_idx is None:
            break
        end = end_idx.start()
        # Skip a closing that is actually a display opener boundary.
        if any(s <= end < e for s, e in consumed):
            pos = start + 1
            continue
        found.append((start, prose[start + 1 : end].strip()))
        pos = end + 1

    return found


def extract_math_expressions(markdown: str) -> list[str]:
    """Extract the TeX content of every math span in ``markdown``.

    Covers ``$...$``, ``$$...$$``, ``\\(...\\)``, and ``\\[...\\]``. Math inside
    fenced code blocks or inline code spans is ignored. Results are in true
    document order (the start position of each span), even when delimiter
    families are mixed; duplicates are preserved (the same expression may
    appear more than once in a manuscript and each occurrence is validated).
    """

    prose = _strip_code_and_fences(markdown)
    # Each family records (start_position, inner_tex). Merging by start
    # position yields document order regardless of which delimiter family
    # appears first in the prose.
    found: list[tuple[int, str]] = []
    found.extend(_extract_dollar_spans(prose))
    for match in _PAREN_INLINE.finditer(prose):
        found.append((match.start(), match.group(1).strip()))
    for match in _BRACKET_DISPLAY.finditer(prose):
        found.append((match.start(), match.group(1).strip()))
    found.sort(key=lambda item: item[0])
    return [expr for _pos, expr in found if expr]


def _renderer_available() -> tuple[bool, str]:
    """Return (available, reason). Probes node + the validator script."""

    node = shutil.which("node")
    if node is None:
        return False, "node is not installed"
    if not _VALIDATOR.is_file():
        return False, f"math validator script not found: {_VALIDATOR}"
    return True, ""


def renderability_report(markdown: str) -> list[MathRejection]:
    """Return the supported math expressions in ``markdown`` the renderer rejects.

    Returns an empty list when the manuscript contains no supported math spans
    (no Node call is made, so math-free manuscripts need no renderer installed).
    Raises ``MathRendererUnavailable`` fail-closed when math is present but the
    configured MathJax renderer cannot be exercised, or when it returns a
    payload that does not satisfy the result contract.
    """

    expressions = extract_math_expressions(markdown)
    if not expressions:
        return []

    available, reason = _renderer_available()
    if not available:
        raise MathRendererUnavailable(
            "report contains math but the configured MathJax renderer is "
            f"unavailable ({reason}); renderability cannot be certified"
        )

    node = shutil.which("node")
    assert node is not None  # guarded above

    try:
        completed = subprocess.run(
            [node, str(_VALIDATOR)],
            input=json.dumps(expressions),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as err:
        raise MathRendererUnavailable(
            "configured MathJax renderer timed out; "
            "renderability cannot be certified"
        ) from err
    except OSError as err:
        raise MathRendererUnavailable(
            f"configured MathJax renderer could not be run: {err}"
        ) from err

    if completed.returncode != 0:
        raise MathRendererUnavailable(
            "configured MathJax renderer failed to start: "
            f"{completed.stderr.strip() or completed.returncode}"
        )

    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError as err:
        raise MathRendererUnavailable(
            "configured MathJax renderer produced unparseable output; "
            "renderability cannot be certified"
        ) from err

    # Validate the complete response contract before consuming any result.
    # The core rule is: N expressions in -> exactly N structurally valid
    # validation results out. A truncated but valid-JSON payload (fewer
    # results than expressions, or results missing the ``ok`` flag) must not
    # silently leave expressions unchecked -- it fails closed.
    invalid_payload = (
        "configured MathJax renderer returned an invalid result payload; "
        "renderability cannot be certified"
    )
    if not isinstance(results, list) or len(results) != len(expressions):
        raise MathRendererUnavailable(invalid_payload)
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise MathRendererUnavailable(invalid_payload)
        # A rejection must carry a non-empty error message; a result that says
        # ``ok: false`` with no usable error is itself a contract violation.
        if not result["ok"]:
            error = result.get("error")
            if not isinstance(error, str) or not error.strip():
                raise MathRendererUnavailable(invalid_payload)

    rejections: list[MathRejection] = []
    for expression, result in zip(expressions, results):
        if not result["ok"]:
            rejections.append(
                MathRejection(expression=expression, error=result["error"])
            )
    return rejections
