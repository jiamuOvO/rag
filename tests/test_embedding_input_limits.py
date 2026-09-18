import io
import urllib.error
from dataclasses import replace

import pytest

from rag.chunker import chunk_pages
from rag.config import Settings
from rag.errors import RagError
from rag.models import Page
from rag.providers import OpenAICompatibleEmbeddingProvider


@pytest.mark.parametrize("text", [
    "Na2CO3 12.34±0.56 789.01 23.45% " * 100,
    "木糖转化为糠醛的收率与反应温度" * 100,
    "9" * 2000,
    "https://example.test/" + "mixed中文Na2CO3" * 300,
])
def test_chunk_budget_preserves_all_content(text):
    pages = [Page(1, text, "text")]
    for overlap in (0, 8):
        chunks = chunk_pages(pages, "p", "test.pdf", target_tokens=40, overlap_tokens=overlap)
        assert all(0 < c.token_count <= 40 for c in chunks)
        assert len({c.text for c in chunks[-2:]}) == 2
        if not overlap:
            assert "".join("".join(c.text.split()) for c in chunks) == "".join(text.split())


def test_overlap_never_creates_a_tail_only_chunk_or_breaks_budget():
    text = "alpha beta gamma delta epsilon zeta eta theta " * 30
    chunks = chunk_pages([Page(1, text, "text")], "p", "test.pdf",
                         target_tokens=12, overlap_tokens=4)
    assert all(0 < chunk.token_count <= 12 and len(chunk.text) <= 512 for chunk in chunks)
    assert all(chunk.text != chunks[index - 1].text for index, chunk in enumerate(chunks) if index)


@pytest.mark.parametrize("status,retryable", [(400, False), (429, True), (503, True)])
def test_embedding_http_error_keeps_reason_without_key(monkeypatch, status, retryable):
    settings = replace(Settings.load(), embedding_base_url="http://example.test/v1",
                       embedding_model="test", embedding_api_key="secret-test-key")

    def reject(*args, **kwargs):
        raise urllib.error.HTTPError("http://example.test", status, "error", {},
                                     io.BytesIO(b'{"error":"maximum length 2048; secret-test-key"}'))

    monkeypatch.setattr("urllib.request.urlopen", reject)
    with pytest.raises(RagError) as error:
        OpenAICompatibleEmbeddingProvider(settings).embed(["test"])
    assert error.value.code == "EMBEDDING_HTTP_ERROR"
    assert error.value.retryable is retryable
    assert "maximum length 2048" in str(error.value)
    assert "secret-test-key" not in str(error.value)
