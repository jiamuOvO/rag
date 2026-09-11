from __future__ import annotations

import re


class ContextualQueryRewriter:
    """Small replaceable boundary for bounded conversational query rewriting.

    It deliberately uses only recent user turns. Deployments may replace this
    class with a model-backed implementation without changing retrieval or the
    persisted original/retrieval question contract.
    """

    REFERENCES_CONTEXT = re.compile(
        r"(?:这些|上述|该|它们?|这个|\b(?:those|these|it|they|that|this)\b)", re.I
    )

    def __init__(self, max_messages: int = 6):
        self.max_messages = max(1, max_messages)

    def rewrite(self, question: str, messages: list[dict]) -> str:
        current = question.strip()
        if not self.REFERENCES_CONTEXT.search(current):
            return current
        recent_users = [str(item.get("content", "")).strip() for item in messages[-self.max_messages:]
                        if item.get("role") == "user" and item.get("content")]
        if not recent_users:
            return current
        previous = recent_users[-1][:1200]
        return f"{previous}\n后续问题：{current}"
