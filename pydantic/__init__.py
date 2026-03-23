from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class FieldInfo:
    __slots__ = ("default", "default_factory")

    def __init__(self, default: Any = ..., default_factory: Optional[Callable[[], Any]] = None) -> None:
        self.default = default
        self.default_factory = default_factory


def Field(*, default: Any = ..., default_factory: Optional[Callable[[], Any]] = None) -> FieldInfo:
    return FieldInfo(default=default, default_factory=default_factory)


class BaseModelMeta(type):
    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: Dict[str, Any]):
        annotations = namespace.get("__annotations__", {})
        field_defaults: Dict[str, FieldInfo] = {}
        for attr_name in annotations:
            attr_value = namespace.get(attr_name, ...)
            if isinstance(attr_value, FieldInfo):
                field_defaults[attr_name] = attr_value
                namespace[attr_name] = attr_value.default if attr_value.default is not ... else None
            else:
                field_defaults[attr_name] = FieldInfo(default=attr_value)
        namespace["__field_defaults__"] = field_defaults
        return super().__new__(mcls, name, bases, namespace)


class BaseModel(metaclass=BaseModelMeta):
    def __init__(self, **data: Any) -> None:
        fields: Dict[str, FieldInfo] = getattr(self, "__field_defaults__", {})
        for field_name, info in fields.items():
            if field_name in data:
                value = data[field_name]
            elif info.default is not ...:
                value = info.default
            elif info.default_factory is not None:
                value = info.default_factory()
            else:
                value = None
            setattr(self, field_name, value)
        for key, value in data.items():
            if key not in fields:
                setattr(self, key, value)

    def dict(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - debug helper
        return dict(self.__dict__)

    def model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.dict()

    class Config:  # pragma: no cover - compatibility shim
        arbitrary_types_allowed = True
