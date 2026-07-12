# kairoslabs — Kairos Python SDK

RAG tracing and agent reliability testing for [Kairos](../../README.md).

```bash
pip install kairoslabs              # core (any retriever)
pip install "kairoslabs[langchain]" # + LangChain callback handler
```

```python
from kairos import KairosTracer

tracer = KairosTracer(api_key="kai_live_xxxx", pipeline_id="your-pipeline-id")

with tracer.trace(query=user_query) as t:
    chunks = retriever.retrieve(user_query)
    t.log_retrieval(chunks)
    answer = llm.generate(chunks, user_query)
    t.log_answer(answer)
```

Traces are batched and sent asynchronously — zero latency impact on your pipeline.
See the [quickstart](../../docs/quickstart.mdx) for the full walkthrough.
