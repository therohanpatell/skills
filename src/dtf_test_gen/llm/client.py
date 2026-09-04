"""A thin Ollama client. Never raises into the UI; failures are reported as status."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


class OllamaError(Exception):
    pass


@dataclass
class OllamaStatus:
    connected: bool
    host: str
    models: list[str] = field(default_factory=list)
    error: str | None = None


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434", timeout: float = 180.0):
        self.host = host.rstrip("/")
        self.timeout = timeout

    def status(self) -> OllamaStatus:
        """Probe the server and list installed models. Never raises."""
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.host}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except httpx.ConnectError:
            return OllamaStatus(
                connected=False, host=self.host,
                error="Could not connect. Is `ollama serve` running?",
            )
        except httpx.TimeoutException:
            return OllamaStatus(connected=False, host=self.host, error="Connection timed out.")
        except Exception as exc:                      # noqa: BLE001 - surfaced to the UI
            return OllamaStatus(connected=False, host=self.host, error=str(exc))
        models = sorted(m.get("name", "") for m in payload.get("models", []) if m.get("name"))
        return OllamaStatus(connected=True, host=self.host, models=models)

    def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
        json_mode: bool = True,
        num_predict: int = 1500,
    ) -> str:
        """One non-streaming completion. Raises OllamaError with a readable message."""
        body: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if system:
            body["system"] = system
        if json_mode:
            body["format"] = "json"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.host}/api/generate", json=body)
                if response.status_code == 404:
                    raise OllamaError(
                        f"Model `{model}` is not installed. Run: ollama pull {model}"
                    )
                response.raise_for_status()
                payload = response.json()
        except httpx.ConnectError as exc:
            raise OllamaError(
                "Ollama is unavailable. Start it with `ollama serve` and try again."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama timed out after {self.timeout:.0f}s. Try a smaller model or Fast mode."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code}.") from exc
        return payload.get("response", "")
