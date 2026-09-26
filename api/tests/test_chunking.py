from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.chunking import smart_split

TOPICS = {
    "cat": ["cat", "cats", "kitten", "feline", "purr"],
    "money": ["stock", "stocks", "bond", "bonds", "interest", "portfolio"],
    "space": ["planet", "orbit", "rocket", "nasa", "moon"],
}


class TopicEmbeddings(Embeddings):
    """Deterministic embedding: one dimension per topic, weighted by keyword hits."""

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        words = text.lower().replace(".", " ").split()
        return [sum(w in kws for w in words) + 0.01 for kws in TOPICS.values()]


class FailingEmbeddings(TopicEmbeddings):
    def embed_documents(self, texts):
        raise RuntimeError("provider down")


def split(text, *, pdf=False, embeddings=TopicEmbeddings(), max_chars=1000, min_chars=50, pct=80, pages=None):
    docs = pages or [Document(page_content=text)]
    return smart_split(
        docs, is_pdf=pdf, max_chars=max_chars, min_chars=min_chars, breakpoint_percentile=pct, embeddings=embeddings
    )


CAT = "My cat likes to purr. The kitten is a small feline and every cat naps a lot."
MONEY = "A portfolio mixes stock and bond holdings. Interest on bonds is paid regularly."
SPACE = "The rocket reached orbit. NASA plans to return to the moon and visit another planet."


def test_topic_shift_starts_new_chunk():
    text = "\n\n".join([CAT, CAT, CAT, MONEY, MONEY, MONEY, SPACE, SPACE])
    chunks = split(text)
    assert [c.page_content.count("cat") > 0 for c in chunks] == [True, False, False]
    assert "portfolio" in chunks[1].page_content and "rocket" not in chunks[1].page_content
    assert "rocket" in chunks[2].page_content


def test_chunks_end_on_paragraph_boundaries_and_respect_max():
    paragraphs = [f"Paragraph {i}. " + "word " * 40 for i in range(20)]
    chunks = split("\n\n".join(p.strip() for p in paragraphs), max_chars=500)
    assert all(len(c.page_content) <= 500 for c in chunks)
    for c in chunks:
        for part in c.page_content.split("\n\n"):
            assert part.startswith("Paragraph ") and part.endswith("word")


def test_markdown_headings_start_chunks_and_prefix_context():
    md = f"# Guide\n\nIntro text about the guide.\n\n## Pets\n\n{CAT}\n\n## Finance\n\n{MONEY}\n\n{MONEY}\n"
    chunks = split(md, min_chars=10)
    assert chunks[0].page_content.startswith("# Guide")
    pets = next(c for c in chunks if "## Pets" in c.page_content)
    assert pets.metadata["section"] == "Guide > Pets" and "kitten" in pets.page_content
    finance = [c for c in chunks if "portfolio" in c.page_content]
    assert all(c.metadata["section"] == "Guide > Finance" for c in finance)
    # A chunk that doesn't start with its heading carries the heading path as context.
    for c in finance:
        assert c.page_content.startswith("## Finance") or c.page_content.startswith("[Guide > Finance]")


def test_long_paragraph_is_split_at_sentences():
    sentences = [f"Sentence number {i} talks about something." for i in range(60)]
    chunks = split(" ".join(sentences), max_chars=300)
    assert all(len(c.page_content) <= 300 for c in chunks)
    assert all(c.page_content.rstrip().endswith(".") for c in chunks)
    assert sum(c.page_content.count("Sentence number") for c in chunks) == 60


def test_code_fence_is_not_split_at_blank_lines():
    md = "Intro.\n\n```python\ndef f():\n\n    return 1\n```\n\nAfter."
    chunks = split(md, min_chars=1000)
    assert len(chunks) == 1
    assert "```python\ndef f():\n\n    return 1\n```" in chunks[0].page_content


def test_pdf_lines_rejoined_into_paragraphs_with_page_range():
    line = "this is a long line of pdf text that fills the whole width of the page"
    page1 = "\n".join([line] * 3 + ["and it ends here."] + [line] * 2)
    page2 = "\n".join([line] * 2 + ["the end."])
    pages = [Document(page_content=page1, metadata={"page": 1}), Document(page_content=page2, metadata={"page": 2})]
    chunks = split("", pdf=True, pages=pages, embeddings=None)
    assert len(chunks) == 1
    body = chunks[0].page_content
    assert "\n" not in body.split("\n\n")[0]  # visual lines rejoined with spaces
    assert "and it ends here.\n\n" in body  # short sentence-final line ends a paragraph
    assert chunks[0].metadata == {"page": 1, "page_end": 2}


def test_embedding_failure_falls_back_to_structure():
    text = "\n\n".join([CAT, MONEY, SPACE] * 10)
    chunks = split(text, embeddings=FailingEmbeddings(), max_chars=400)
    assert chunks and all(len(c.page_content) <= 400 for c in chunks)


def test_empty_input():
    assert split("", pages=[]) == []
    assert split("# Only a heading\n") == []
