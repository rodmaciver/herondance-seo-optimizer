"""Thin multi-provider LLM wrapper used by both the generator and judge stages.

`call_model` is the single chokepoint: every provider is forced into the
same "give me back JSON matching this pydantic schema" contract using each
provider's native structured-output / tool-use mechanism.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Type

import yaml
from pydantic import BaseModel

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_models_config() -> dict:
    with open(CONFIG_DIR / "models.yaml") as f:
        return yaml.safe_load(f)


def _strict_json_schema(model: Type[BaseModel]) -> dict:
    """Pydantic JSON schema, flattened and tightened for OpenAI strict mode."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    defs = schema.pop("$defs", None)

    def _tighten(node: dict) -> None:
        if node.get("type") == "object":
            node["additionalProperties"] = False
            props = node.get("properties", {})
            node["required"] = list(props.keys())
            for prop in props.values():
                _tighten(prop)
        elif node.get("type") == "array":
            _tighten(node.get("items", {}))

    if defs:
        for d in defs.values():
            _tighten(d)
        schema["$defs"] = defs
    _tighten(schema)
    return schema


def available_providers() -> dict[str, bool]:
    return {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
    }


def get_model_config(model_id: str) -> dict:
    cfg = _load_models_config()
    for m in cfg["models"]:
        if m["id"] == model_id:
            return m
    raise ValueError(f"Unknown model id: {model_id}")


def _call_anthropic(model: str, system: str, user: str, schema_model: Type[BaseModel], temperature: float) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    schema = _strict_json_schema(schema_model)
    tool_name = "submit_result"
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"name": tool_name, "description": "Submit the structured result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Anthropic response contained no tool_use block")


def _call_openai_compatible(
    model: str,
    system: str,
    user: str,
    schema_model: Type[BaseModel],
    temperature: float,
    base_url: str | None,
    api_key_env: str,
) -> dict:
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    schema = _strict_json_schema(schema_model)

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_model.__name__, "schema": schema, "strict": True},
            },
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        # Fallback for providers (e.g. Gemini's OpenAI-compatible endpoint)
        # that don't fully support json_schema strict mode.
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": user
                    + "\n\nRespond with ONLY a single JSON object matching this schema:\n"
                    + json.dumps(schema),
                },
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)


def call_model(
    model_id: str,
    system: str,
    user: str,
    response_schema: Type[BaseModel],
    temperature: float = 0.7,
) -> dict:
    """Call the named model and return a dict matching `response_schema`."""
    config = get_model_config(model_id)
    provider = config["provider"]
    model = config["model"]

    if provider == "anthropic":
        return _call_anthropic(model, system, user, response_schema, temperature)
    if provider == "openai":
        return _call_openai_compatible(model, system, user, response_schema, temperature, None, "OPENAI_API_KEY")
    if provider == "openai_compatible":
        return _call_openai_compatible(
            model, system, user, response_schema, temperature, config["base_url"], config["api_key_env"]
        )
    raise ValueError(f"Unsupported provider: {provider}")
