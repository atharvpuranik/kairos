from unittest.mock import MagicMock

from langchain.chains import RetrievalQA
from langchain_core.documents import Document
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.retrievers import BaseRetriever

from kairos.integrations.langchain import KairosCallbackHandler


class FixedRetriever(BaseRetriever):
    def _get_relevant_documents(self, query, *, run_manager=None):
        return [
            Document(
                page_content="Refunds within 30 days.",
                metadata={"doc_id": "doc-1", "score": 0.9},
            )
        ]


def make_handler():
    handler = KairosCallbackHandler(api_key="kai_live_x", pipeline_id="p1", api_url="http://localhost:9999")
    handler._client.send_trace = MagicMock()
    return handler


def test_retrieval_qa_sends_exactly_one_trace_with_correct_data():
    handler = make_handler()
    llm = FakeListLLM(responses=["You can get a refund within 30 days."])
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=FixedRetriever())

    result = qa.invoke(
        {"query": "What is the refund policy?"}, config={"callbacks": [handler]}
    )

    assert result["result"] == "You can get a refund within 30 days."
    handler._client.send_trace.assert_called_once()
    payload = handler._client.send_trace.call_args[0][0]
    assert payload.query == "What is the refund policy?"
    assert payload.final_answer == "You can get a refund within 30 days."
    assert len(payload.retrieved_chunks) == 1
    assert payload.retrieved_chunks[0].doc_id == "doc-1"


def test_nested_sub_chain_runs_do_not_reset_or_duplicate_trace():
    """Regression test: RetrievalQA is a nested chain (QA -> combine-docs ->
    LLM chain), each firing its own on_chain_start/on_chain_end. Only the
    outermost run should produce a trace."""
    handler = make_handler()
    llm = FakeListLLM(responses=["Answer text."])
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=FixedRetriever())

    qa.invoke({"query": "q"}, config={"callbacks": [handler]})

    assert handler._client.send_trace.call_count == 1


def test_constructor_level_callbacks_do_not_crash_but_capture_nothing():
    """Documents the known LangChain limitation: callbacks passed to
    from_chain_type(..., callbacks=[handler]) don't propagate to retriever/
    LLM sub-runs, so no chunks are captured and no trace is sent. This must
    fail silently, not raise."""
    handler = make_handler()
    llm = FakeListLLM(responses=["Answer text."])
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=FixedRetriever(), callbacks=[handler])

    qa.invoke({"query": "q"})

    handler._client.send_trace.assert_not_called()
