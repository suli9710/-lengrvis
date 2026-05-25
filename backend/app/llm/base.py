from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.llm.prompts import load_prompt, render_prompt


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise NotImplementedError("Embedding is not configured for this provider.")

    async def rerank(self, query: str, documents: list[str]) -> list[int]:
        raise NotImplementedError("Rerank is not configured for this provider.")

    async def vision(self, image_path: str, prompt: str) -> str:
        raise NotImplementedError("Vision is not configured for this provider.")

    async def ocr(self, image_path: str) -> str:
        raise NotImplementedError("OCR is not configured for this provider.")

    async def summarize(self, text: str) -> str:
        return await self.chat(
            [
                {"role": "system", "content": load_prompt("llm_summarize_system.md")},
                {"role": "user", "content": render_prompt("llm_summarize_user.md", {"text": text})},
            ]
        )

    async def classify(self, text: str, labels: list[str]) -> str:
        return await self.chat(
            [
                {"role": "system", "content": render_prompt("llm_classify_system.md", {"labels": ", ".join(labels)})},
                {"role": "user", "content": render_prompt("llm_classify_user.md", {"text": text})},
            ]
        )
