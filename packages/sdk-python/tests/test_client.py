import json

import httpx
import pytest
import respx
from httpx import Response

from kairos.client import KairosClient
from kairos.models import RetrievedChunk, TracePayload


def make_payload(query: str = "q") -> TracePayload:
    return TracePayload(
        pipeline_id="11111111-1111-1111-1111-111111111111",
        query=query,
        retrieved_chunks=[RetrievedChunk(content="c", score=0.9, doc_id="d1")],
        final_answer="a",
        latency_ms=10,
    )


def make_client(**kwargs) -> KairosClient:
    # long flush_interval so only explicit flush() sends during tests
    return KairosClient(
        api_key="kai_live_test", api_url="http://example.test", flush_interval=60.0, **kwargs
    )


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KAIROS_API_KEY", raising=False)
    with pytest.raises(ValueError):
        KairosClient(api_url="http://example.test")


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("KAIROS_API_KEY", "kai_live_env")
    client = KairosClient(api_url="http://example.test", flush_interval=60.0)
    assert client.api_key == "kai_live_env"


@respx.mock
def test_flush_posts_batch_with_auth_header():
    route = respx.post("http://example.test/v1/traces/batch").mock(
        return_value=Response(202, json={"trace_ids": ["x"], "status": "queued"})
    )
    client = make_client()
    client.send_trace(make_payload("first"))
    client.send_trace(make_payload("second"))
    client.flush()

    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer kai_live_test"
    body = json.loads(request.content)
    assert [t["query"] for t in body["traces"]] == ["first", "second"]
    assert body["traces"][0]["retrieved_chunks"][0]["doc_id"] == "d1"


@respx.mock
def test_reaching_flush_at_triggers_send_without_explicit_flush():
    route = respx.post("http://example.test/v1/traces/batch").mock(
        return_value=Response(202, json={"trace_ids": [], "status": "queued"})
    )
    client = make_client(flush_at=3)
    for i in range(3):
        client.send_trace(make_payload(f"q{i}"))
    client.flush()  # synchronizes; the batch itself was dispatched at flush_at

    assert route.called
    total_sent = sum(
        len(json.loads(call.request.content)["traces"]) for call in route.calls
    )
    assert total_sent == 3


@respx.mock
def test_flush_does_not_raise_on_4xx():
    respx.post("http://example.test/v1/traces/batch").mock(
        return_value=Response(401, json={"detail": "Invalid API key"})
    )
    client = make_client()
    client.send_trace(make_payload())
    client.flush()  # must not raise


@respx.mock
def test_flush_does_not_raise_on_connection_error():
    respx.post("http://example.test/v1/traces/batch").mock(
        side_effect=httpx.ConnectError("boom")
    )
    client = make_client()
    client.send_trace(make_payload())
    client.flush()  # must not raise — non-blocking SDK must never break the caller


@respx.mock
def test_bounded_buffer_drops_when_full():
    route = respx.post("http://example.test/v1/traces/batch").mock(
        return_value=Response(202, json={"trace_ids": [], "status": "queued"})
    )
    client = make_client(max_buffer_size=5, flush_at=1000)
    for i in range(10):
        client.send_trace(make_payload(f"q{i}"))
    client.flush()

    total_sent = sum(
        len(json.loads(call.request.content)["traces"]) for call in route.calls
    )
    assert total_sent == 5  # the rest were dropped, not queued unboundedly
    assert client._dropped_count == 5


@respx.mock
def test_empty_flush_sends_nothing():
    route = respx.post("http://example.test/v1/traces/batch").mock(
        return_value=Response(202, json={"trace_ids": [], "status": "queued"})
    )
    client = make_client()
    client.flush()
    assert not route.called
