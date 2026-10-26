"""LLM integration helpers for the Job Server.

The Job Server defers actual model calls to an injected async stream
callable so the HTTP layer, the DB layer, and tests can exercise the
question-streaming pipeline without instantiating
``trading.tools.llm_client.LLMClient`` or making a network request.
"""
