"""Paragraph- and context-aware chunking.

Instead of cutting text every N characters, a document is:

1. split into paragraphs (blank lines; for PDFs, which rarely contain blank lines, a line that ends a
   sentence and is visibly shorter than a full line also ends a paragraph). Markdown headings and fenced
   code blocks are kept as their own units.
2. oversized paragraphs are broken at sentence boundaries, so no unit exceeds ``max_chars``.
3. consecutive units are grouped into chunks. A new chunk starts at a heading, when the next unit would
   push the chunk past ``max_chars``, or when the topic shifts: the embedding distance between adjacent
   units is above the ``breakpoint_percentile`` of all adjacent distances in the document. Chunks shorter
   than ``min_chars`` are not closed on a topic shift, which avoids one-line fragments.
4. each chunk is prefixed with its heading path (e.g. "Setup > Database") when the chunk itself does not
   start with that heading, so it keeps its context once retrieved on its own.

Chunks always end on a paragraph (or, for very long paragraphs, sentence) boundary, so no overlap is needed.
"""

import logging
import math
import re
from dataclasses import dataclass, field

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BLANK_LINES = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
_LIST_ITEM = re.compile(r"^\s*([-*+•▪◦]|\d+[.)])\s+")
_TERMINAL = re.compile(r"[.!?:;][\"')\]]*$")


@dataclass
class _Unit:
    text: str
    page: int | None
    headings: tuple[str, ...]
    is_heading: bool = False


@dataclass
class _Chunk:
    units: list[_Unit] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(u.text) for u in self.units) + 2 * max(len(self.units) - 1, 0)


# ---------------------------------------------------------------- paragraph detection


def _pdf_paragraphs(text: str) -> list[str]:
    """pypdf gives one line per visual line. Rejoin lines into paragraphs."""
    if _BLANK_LINES.search(text):
        blocks = _BLANK_LINES.split(text)
    else:
        blocks = [text]
    paragraphs: list[str] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # A "full" line is close to the typical width of long lines on this page.
        widths = sorted(len(ln) for ln in lines)
        full = widths[int(len(widths) * 0.75)] if widths else 0
        current: list[str] = []
        for ln in lines:
            if current and _LIST_ITEM.match(ln):
                paragraphs.append(" ".join(current))
                current = []
            current.append(ln.strip())
            if _TERMINAL.search(ln) and len(ln) < 0.8 * full:
                paragraphs.append(" ".join(current))
                current = []
        if current:
            paragraphs.append(" ".join(current))
    # Re-join hyphenated line breaks ("docu- ment" -> "document").
    return [re.sub(r"(\w)- (\w)", r"\1\2", p) for p in paragraphs]


def _text_paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs; headings and fenced code blocks are their own paragraph."""
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush():
        if current and any(ln.strip() for ln in current):
            paragraphs.append("\n".join(current).strip("\n"))
        current.clear()

    for line in text.split("\n"):
        if _FENCE.match(line):
            if not in_fence:
                flush()
            current.append(line)
            if in_fence:
                flush()
            in_fence = not in_fence
            continue
        if in_fence:
            current.append(line)
        elif not line.strip():
            flush()
        elif _HEADING.match(line):
            flush()
            paragraphs.append(line.strip())
        else:
            current.append(line)
    flush()
    return paragraphs


def _units(pages: list[Document], is_pdf: bool, max_chars: int) -> list[_Unit]:
    headings: list[tuple[int, str]] = []  # (level, title) stack
    units: list[_Unit] = []
    fallback = RecursiveCharacterTextSplitter(chunk_size=max_chars, chunk_overlap=0)
    for page in pages:
        text = page.page_content.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = _pdf_paragraphs(text) if is_pdf else _text_paragraphs(text)
        for para in paragraphs:
            if m := _HEADING.match(para):
                level = len(m.group(1))
                headings = [h for h in headings if h[0] < level] + [(level, m.group(2))]
                units.append(_Unit(para, page.metadata.get("page"), tuple(t for _, t in headings), True))
                continue
            path = tuple(t for _, t in headings)
            for piece in _fit(para, max_chars, fallback):
                units.append(_Unit(piece, page.metadata.get("page"), path))
    return units


def _fit(paragraph: str, max_chars: int, fallback: RecursiveCharacterTextSplitter) -> list[str]:
    """Break a paragraph that is longer than max_chars at sentence boundaries (hard split as last resort)."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    if _FENCE.match(paragraph):
        return fallback.split_text(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(fallback.split_text(sentence))
        elif current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return pieces


# ---------------------------------------------------------------- semantic grouping


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norm if norm else 1.0


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _topic_breaks(units: list[_Unit], embeddings: Embeddings | None, percentile: float) -> list[bool]:
    """breaks[i] is True when unit i+1 starts a new topic relative to unit i."""
    if embeddings is None or len(units) < 3:
        return [False] * max(len(units) - 1, 0)
    try:
        vectors = embeddings.embed_documents([u.text for u in units])
    except Exception:  # grouping is an optimisation; structural chunking still works without it
        logger.warning("Embedding paragraphs for semantic grouping failed; using structure only.", exc_info=True)
        return [False] * (len(units) - 1)
    distances = [_cosine_distance(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
    threshold = _percentile(distances, percentile)
    return [d > threshold for d in distances]


def smart_split(
    pages: list[Document],
    *,
    is_pdf: bool,
    max_chars: int,
    min_chars: int,
    breakpoint_percentile: float,
    embeddings: Embeddings | None = None,
) -> list[Document]:
    units = _units(pages, is_pdf, max_chars)
    breaks = _topic_breaks(units, embeddings, breakpoint_percentile)

    chunks: list[_Chunk] = []
    current = _Chunk()
    for i, unit in enumerate(units):
        if current.units:
            too_big = current.size + 2 + len(unit.text) > max_chars
            # A heading always opens a new chunk, unless the chunk so far holds only headings.
            new_section = unit.is_heading and not all(u.is_heading for u in current.units)
            topic_shift = breaks[i - 1] and current.size >= min_chars
            if too_big or new_section or topic_shift:
                chunks.append(current)
                current = _Chunk()
        current.units.append(unit)
    if current.units:
        chunks.append(current)

    docs: list[Document] = []
    for chunk in chunks:
        body = "\n\n".join(u.text for u in chunk.units)
        if all(u.is_heading for u in chunk.units):
            continue  # a heading with no body (e.g. at the end of a file) adds nothing searchable
        first = chunk.units[0]
        section = " > ".join(first.headings)
        if section and not first.is_heading:
            body = f"[{section}]\n\n{body}"
        metadata: dict = {}
        if section:
            metadata["section"] = section
        pages_in_chunk = [u.page for u in chunk.units if u.page is not None]
        if pages_in_chunk:
            metadata["page"] = pages_in_chunk[0]
            if pages_in_chunk[-1] != pages_in_chunk[0]:
                metadata["page_end"] = pages_in_chunk[-1]
        docs.append(Document(page_content=body, metadata=metadata))
    return docs
