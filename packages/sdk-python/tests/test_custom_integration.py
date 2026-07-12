import pytest

from kairos.integrations.custom import call_retriever, normalize_chunks


class DocLike:
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


def test_call_retriever_plain_callable():
    result = call_retriever(lambda q: [q.upper()], "hi")
    assert result == ["HI"]


def test_call_retriever_dot_retrieve():
    class R:
        def retrieve(self, query):
            return [query]

    assert call_retriever(R(), "hi") == ["hi"]


def test_call_retriever_get_relevant_documents():
    class R:
        def get_relevant_documents(self, query):
            return [query]

    assert call_retriever(R(), "hi") == ["hi"]


def test_call_retriever_unsupported_type_raises():
    with pytest.raises(TypeError):
        call_retriever(object(), "hi")


def test_normalize_chunks_from_dicts():
    chunks = normalize_chunks([{"content": "c1", "score": 0.8, "doc_id": "d1"}])
    assert chunks == [{"content": "c1", "score": 0.8, "doc_id": "d1", "metadata": None}]


def test_normalize_chunks_from_doc_like_objects():
    docs = [DocLike("hello", {"source": "doc-9", "score": 0.7})]
    chunks = normalize_chunks(docs)
    assert chunks[0]["content"] == "hello"
    assert chunks[0]["doc_id"] == "doc-9"
    assert chunks[0]["score"] == 0.7


def test_normalize_chunks_from_tuples():
    chunks = normalize_chunks([("some text", 0.42)])
    assert chunks[0]["content"] == "some text"
    assert chunks[0]["score"] == 0.42


def test_normalize_chunks_from_plain_strings():
    chunks = normalize_chunks(["just text"])
    assert chunks[0]["content"] == "just text"
    assert chunks[0]["score"] == 0.0
