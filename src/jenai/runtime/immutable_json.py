"""Recursively immutable JSON values for Runtime execution contracts."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

type FrozenJsonScalar = str | int | float | bool | None
type FrozenJsonValue = FrozenJsonScalar | tuple[FrozenJsonValue, ...] | FrozenJsonObject


class FrozenJsonObject(Mapping[str, FrozenJsonValue]):
    """Detached read-only JSON object with recursively frozen children."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = MappingProxyType(
            {key: freeze_json_value(value) for key, value in values.items()}
        )

    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, _memo: dict[int, object]) -> FrozenJsonObject:
        return self


def freeze_json_object(value: object) -> FrozenJsonObject:
    """Validate, detach, and recursively freeze one JSON object."""

    if isinstance(value, FrozenJsonObject):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("value must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise TypeError("JSON object keys must be strings")
    return FrozenJsonObject(value)


def freeze_json_value(value: object) -> FrozenJsonValue:
    """Return a detached recursive immutable representation of a JSON value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return freeze_json_object(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(item) for item in value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def thaw_json_object(value: Mapping[str, object]) -> dict[str, object]:
    """Create a JSON-serializable detached object for hashing or transport."""

    return {key: thaw_json_value(item) for key, item in value.items()}


def thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


type ImmutableJsonObject = Annotated[
    Mapping[str, object],
    AfterValidator(freeze_json_object),
    PlainSerializer(thaw_json_object, return_type=dict[str, object]),
]
