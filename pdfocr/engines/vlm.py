"""OCR through a vision LLM served on your own network.

Talks the OpenAI chat-completions dialect, which Ollama, vLLM, LM Studio, llama.cpp and
SGLang all speak, so any of these works as the backend:

    ollama pull qwen2.5vl:7b                          # PDFOCR_VLM_MODEL=qwen2.5vl:7b
    vllm serve deepseek-ai/DeepSeek-OCR --port 8001   # PDFOCR_VLM_MODEL=deepseek-ai/DeepSeek-OCR
"""

from __future__ import annotations

import base64

import httpx

from ..config import Settings
from ..config import settings as default_settings
from ..models import EngineInfo
from .base import Engine, OcrError, PageRequest

# Models that emit a wrapper the transcription has to be dug out of.
_FENCES = ("```markdown", "```md", "```text", "```")


class VlmEngine(Engine):
    name = "vlm"

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or default_settings

    # -- helpers ---------------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.vlm_api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, png: bytes) -> dict:
        data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return {
            "model": self.config.vlm_model,
            "temperature": self.config.vlm_temperature,
            "max_tokens": self.config.vlm_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.config.vlm_prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        }

    @staticmethod
    def _clean(text: str) -> str:
        text = text.strip()
        for fence in _FENCES:
            if text.startswith(fence) and text.endswith("```"):
                text = text[len(fence) : -3]
                break
        return text.strip()

    # -- engine API ------------------------------------------------------------
    def run(self, request: PageRequest) -> str:
        url = self.config.vlm_base_url.rstrip("/") + "/chat/completions"
        try:
            response = httpx.post(
                url,
                headers=self._headers,
                json=self._payload(request.image.png),
                timeout=self.config.vlm_timeout,
            )
        except httpx.HTTPError as exc:
            raise OcrError(f"vision model at {url} unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise OcrError(
                f"vision model returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise OcrError(f"unexpected response from vision model: {response.text[:300]}") from exc

        if isinstance(content, list):  # some servers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return self._clean(content or "")

    def check(self) -> EngineInfo:
        url = self.config.vlm_base_url.rstrip("/") + "/models"
        detail = f"{self.config.vlm_model} @ {self.config.vlm_base_url}"
        try:
            response = httpx.get(url, headers=self._headers, timeout=5.0)
        except httpx.HTTPError as exc:
            return EngineInfo(
                name=self.name, available=False, detail=f"{detail} - unreachable: {exc}"
            )
        if response.status_code >= 400:
            return EngineInfo(
                name=self.name, available=False, detail=f"{detail} - HTTP {response.status_code}"
            )
        return EngineInfo(name=self.name, available=True, detail=detail)
