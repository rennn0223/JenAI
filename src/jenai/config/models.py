from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jenai.schemas import ModelBindings


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    base_url: str | None = None
    api_key_env: str | None = None

    @field_validator("name", "provider")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "0.1.0"
    active_provider: str | None = None
    provider_profiles: dict[str, ProviderProfile] = Field(default_factory=dict)
    model_bindings: ModelBindings | None = None
    locations_path: str | None = None
    route_adapter: str = "stub"
    created_by_setup: bool = False

    def is_complete(self) -> bool:
        return (
            self.active_provider is not None
            and self.active_provider in self.provider_profiles
            and self.model_bindings is not None
        )

    def active_profile(self) -> ProviderProfile | None:
        if self.active_provider is None:
            return None
        return self.provider_profiles.get(self.active_provider)

    def resolved_locations_path(self, config_path: Path) -> Path | None:
        if self.locations_path is None:
            return None
        path = Path(self.locations_path).expanduser()
        if path.is_absolute():
            return path
        return config_path.parent / path

