"""Ollama HTTP client using only the Python standard library."""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


class OllamaError(Exception):
    pass


@dataclass
class OllamaStatus:
    connected: bool
    host: str
    models: list[str] = field(default_factory=list)
    error: str | None = None


class OllamaClient:
    def __init__(self, host="http://localhost:11434", timeout=180.0):
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _request(self, endpoint, body=None, timeout=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.host + endpoint, data=data,
                          headers={"Content-Type": "application/json"})
        try:
            # Never route local model prompts through an environment HTTP proxy.
            with build_opener(ProxyHandler({})).open(request, timeout=timeout or self.timeout) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise OllamaError("Ollama returned a non-object JSON response.")
            if payload.get("error"):
                raise OllamaError(str(payload["error"]))
            return payload
        except HTTPError as exc:
            detail = ""
            try:
                error_body = json.loads(exc.read(4096))
                if isinstance(error_body, dict):
                    detail = str(error_body.get("error", ""))[:1000]
            except (ValueError, OSError):
                pass
            if exc.code == 404 and body:
                raise OllamaError(f"Model `{body['model']}` not found. Run: ollama pull {body['model']}") from exc
            raise OllamaError(f"Ollama returned HTTP {exc.code}. {detail}".strip()) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaError(f"Ollama timed out after {timeout or self.timeout:.0f}s.") from exc
        except URLError as exc:
            raise OllamaError(f"Ollama is unavailable at {self.host}. Start `ollama serve`. {exc.reason}") from exc
        except (ValueError, OSError) as exc:
            raise OllamaError(f"Invalid Ollama response or connection: {exc}") from exc

    def status(self):
        try:
            payload = self._request("/api/tags", timeout=5.0)
            models = sorted(m["name"] for m in payload.get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str))
            return OllamaStatus(True, self.host, models)
        except Exception as exc:
            return OllamaStatus(False, self.host, error=str(exc))

    def generate(self, model, prompt, system=None, temperature=0.1,
                 json_mode=True, num_predict=1500):
        body = {"model": model, "prompt": prompt, "stream": False,
                "options": {"temperature": temperature, "num_predict": num_predict}}
        if system:
            body["system"] = system
        if json_mode:
            body["format"] = "json"
        payload = self._request("/api/generate", body)
        response = payload.get("response")
        if not isinstance(response, str):
            raise OllamaError("Ollama response is missing its text 'response' field.")
        return response
