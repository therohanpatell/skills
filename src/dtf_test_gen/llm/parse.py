"""Extract and validate the model's JSON, with one repair attempt."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from dtf_test_gen.models.llm import LLMAnalysis


class LLMParseError(Exception):
    pass


def _extract_json_object(text: str) -> str | None:
    """Pull the first balanced {...} out of a response that may include prose."""
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_llm_json(text: str) -> LLMAnalysis:
    """Validate a model response into LLMAnalysis, raising LLMParseError on failure."""
    if not text or not text.strip():
        raise LLMParseError("The model returned an empty response.")
    candidate = _extract_json_object(text)
    if candidate is None:
        raise LLMParseError("No JSON object found in the model response.")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # A trailing comma is the single most common failure; try once to fix it.
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError:
            raise LLMParseError(f"Response is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LLMParseError("The model returned JSON that is not an object.")
    # Tolerate a model that wraps its answer or uses a near-miss key name.
    for wrapper in ("analysis", "result", "output", "response"):
        if wrapper in payload and isinstance(payload[wrapper], dict):
            payload = payload[wrapper]
            break
    try:
        return LLMAnalysis.model_validate(payload)
    except ValidationError as exc:
        raise LLMParseError(f"Response did not match the expected shape: {exc.error_count()} problem(s).") from exc


REPAIR_INSTRUCTION = (
    "Your previous response was not valid JSON matching the required shape. "
    "Return ONLY the JSON object, with no prose and no markdown fence. "
    "If you have nothing to add, return: "
    '{"transformations": [], "column_roles": [], "scenario_hints": [], "notes": []}'
)
