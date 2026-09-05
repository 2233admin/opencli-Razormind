from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel

PAW_MAX_PROMPT_CHARS = 8_192
PAW_MAX_TOKENS = 512
PAW_CONFIG_KEYS = frozenset({"max_tokens", "output_schema"})


def validate_paw_agent_config(
    processor_type: str, prompt_template: str, processor_config: dict[str, Any]
) -> None:
    if processor_type != "paw":
        return
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ValueError("paw.prompt_template_required")
    if len(prompt_template) > PAW_MAX_PROMPT_CHARS:
        raise ValueError("paw.prompt_template_too_large")
    if not isinstance(processor_config, dict) or set(processor_config) - PAW_CONFIG_KEYS:
        raise ValueError("paw.processor_config_invalid")
    max_tokens = processor_config.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= PAW_MAX_TOKENS
    ):
        raise ValueError("paw.max_tokens_invalid")
    output_schema = processor_config.get("output_schema")
    if output_schema is not None:
        if not isinstance(output_schema, dict):
            raise ValueError("paw.output_schema_invalid")
        try:
            Draft202012Validator.check_schema(output_schema)
        except SchemaError as exc:
            raise ValueError("paw.output_schema_invalid") from exc


class AIAgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    processor_type: str = "claude"
    model: str | None = None
    prompt_template: str = ""
    processor_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    provider_id: str | None = None


class AIAgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    processor_type: str | None = None
    model: str | None = None
    prompt_template: str | None = None
    processor_config: dict[str, Any] | None = None
    enabled: bool | None = None
    provider_id: str | None = None


class AIAgentRead(UTCModel):
    id: str
    name: str
    description: str | None
    processor_type: str
    model: str | None
    prompt_template: str
    processor_config: dict[str, Any]
    enabled: bool
    provider_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
