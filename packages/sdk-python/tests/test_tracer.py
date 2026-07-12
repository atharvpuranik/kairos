from unittest.mock import MagicMock

import pytest

from kairos.tracer import KairosTracer


@pytest.fixture
def tracer(monkeypatch):
    monkeypatch.setenv("KAIROS_API_KEY", "kai_live_test")
    t = KairosTracer(pipeline_id="pipe-1")
    t.client.send_trace = MagicMock()
    return t


def test_manual_trace_happy_path_sends_payload(tracer):
    with tracer.trace(query="what is the refund policy?") as t:
        t.log_retrieval([{"content": "c", "score": 0.9, "doc_id": "d1"}])
        t.log_answer("30 days")

    tracer.client.send_trace.assert_called_once()
    payload = tracer.client.send_trace.call_args[0][0]
    assert payload.query == "what is the refund policy?"
    assert payload.final_answer == "30 days"
    assert payload.pipeline_id == "pipe-1"
    assert payload.retrieved_chunks[0].doc_id == "d1"
    assert payload.latency_ms >= 0


def test_trace_without_answer_is_not_sent(tracer):
    with tracer.trace(query="q") as t:
        t.log_retrieval([{"content": "c", "score": 0.9, "doc_id": "d1"}])
        # never calls log_answer

    tracer.client.send_trace.assert_not_called()


def test_trace_without_retrieval_is_not_sent(tracer):
    with tracer.trace(query="q") as t:
        t.log_answer("a")
        # never calls log_retrieval

    tracer.client.send_trace.assert_not_called()


def test_exception_inside_trace_is_not_sent_and_propagates(tracer):
    with pytest.raises(RuntimeError):
        with tracer.trace(query="q") as t:
            t.log_retrieval([{"content": "c", "score": 0.9, "doc_id": "d1"}])
            t.log_answer("a")
            raise RuntimeError("pipeline blew up")

    tracer.client.send_trace.assert_not_called()


def test_trace_requires_pipeline_id(monkeypatch):
    monkeypatch.setenv("KAIROS_API_KEY", "kai_live_test")
    monkeypatch.delenv("KAIROS_PIPELINE_ID", raising=False)
    t = KairosTracer()
    with pytest.raises(ValueError):
        t.trace(query="q")


def test_wrap_logs_retrieval_when_trace_active(tracer):
    def fake_retriever(query: str):
        return [{"content": f"result for {query}", "score": 0.5, "doc_id": "r1"}]

    wrapped = tracer.wrap(fake_retriever)

    with tracer.trace(query="q") as t:
        chunks = wrapped("q")
        assert chunks[0]["content"] == "result for q"
        t.log_answer("a")

    tracer.client.send_trace.assert_called_once()
    payload = tracer.client.send_trace.call_args[0][0]
    assert payload.retrieved_chunks[0].doc_id == "r1"


def test_wrap_passes_through_untraced_outside_active_trace(tracer):
    def fake_retriever(query: str):
        return [{"content": "x", "score": 1.0, "doc_id": "r1"}]

    wrapped = tracer.wrap(fake_retriever)
    result = wrapped("q")

    assert result[0]["doc_id"] == "r1"
    tracer.client.send_trace.assert_not_called()
