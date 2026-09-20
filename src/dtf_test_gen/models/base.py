"""Dataclass serialization and validation at JSON/cache boundaries; no dependencies."""
from __future__ import annotations

import copy
import json
import types
from dataclasses import fields
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints


class ValidationError(ValueError):
    pass


def _decode(value, annotation):
    if annotation is Any:
        return value
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        for candidate in args:
            try:
                return _decode(value, candidate)
            except (ValueError, TypeError):
                pass
        raise ValidationError(f"Value does not match {annotation}")
    if origin is list:
        if not isinstance(value, list):
            raise ValidationError("Expected a list")
        return [_decode(v, args[0]) for v in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ValidationError("Expected an object")
        return {_decode(k, args[0]): _decode(v, args[1]) for k, v in value.items()}
    if isinstance(annotation, type) and issubclass(annotation, Model):
        return annotation.model_validate(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if not isinstance(value, annotation) or (annotation is int and isinstance(value, bool)):
        raise ValidationError(f"Expected {annotation}, got {type(value).__name__}")
    return value


def _encode(value):
    if isinstance(value, Model):
        return value.model_dump()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


class Model:
    @classmethod
    def model_validate(cls, payload):
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, dict):
            raise ValidationError(f"Expected an object for {cls.__name__}")
        hints = get_type_hints(cls)
        try:
            return cls(**{f.name: _decode(payload[f.name], hints[f.name])
                          for f in fields(cls) if f.name in payload})
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"{cls.__name__}: {exc}") from exc

    @classmethod
    def model_validate_json(cls, text):
        return cls.model_validate(json.loads(text))

    def model_dump(self):
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}

    def model_dump_json(self, **kwargs):
        return json.dumps(self.model_dump(), **kwargs)

    def model_copy(self, *, deep=False):
        return copy.deepcopy(self) if deep else copy.copy(self)
