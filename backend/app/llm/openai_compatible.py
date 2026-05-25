from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import AppSettings
from app.llm.base import LLMProvider
from app.llm.prompts import load_prompt, render_prompt


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.requires_openai_auth and self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def _chat_endpoint(self) -> str:
        base_url = self.settings.base_url.rstrip("/")
        if self.settings.wire_api.lower() == "responses":
            return f"{base_url}/responses"
        return f"{base_url}/chat/completions"

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        if self.settings.wire_api.lower() == "responses":
            return await self._responses_chat(messages, model=model, temperature=temperature, tools=tools)

        payload: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(
                self._chat_endpoint(),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"].get("content") or ""

    async def _responses_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        input_items = [
            {"role": message["role"], "content": message.get("content", "")}
            for message in messages
            if message.get("role") in {"developer", "system", "user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": model or self.settings.model,
            "input": input_items,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_output_tokens": self.settings.max_tokens,
            "store": not self.settings.disable_response_storage,
        }
        if self.settings.model_reasoning_effort:
            payload["reasoning"] = {"effort": self.settings.model_reasoning_effort}
        if tools:
            payload["tools"] = tools
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(
                self._chat_endpoint(),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        return self._extract_responses_text(response.json())

    def _extract_responses_text(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]

        parts: list[str] = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        schema_prompt = {
            "role": "system",
            "content": render_prompt("structured_json_schema.md", {"schema": json.dumps(output_schema)}),
        }
        content = await self.chat([schema_prompt, *messages], temperature=0)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        payload = {"model": model or self.settings.embedding_model, "input": texts}
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(
                f"{self.settings.base_url.rstrip('/')}/embeddings",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
        import base64
        from pathlib import Path

        path = Path(image_path)
        if not path.exists():
            return f"[vision] file not found: {image_path}"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lstrip(".").lower() or "png"
        mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
        data_url = f"data:{mime};base64,{encoded}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        target_model = model or self.settings.vision_model or self.settings.model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(
                f"{self.settings.base_url.rstrip('/')}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"].get("content") or ""

    async def ocr(self, image_path: str) -> str:
        return await self.vision(image_path, load_prompt("vision_ocr.md"))
