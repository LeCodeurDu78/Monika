"""Settings: the kernel is gone, and the cross-field rules still refuse impossible values."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from monika.config import Settings


def test_kernel_settings_are_gone() -> None:
    assert "kernel_enabled" not in Settings.model_fields
    assert "kernel_socket" not in Settings.model_fields


def test_loop_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, loop_warn_after=5, loop_stop_after=3)


def test_ollama_and_openrouter_presets_fill_the_defaults() -> None:
    ollama = Settings(_env_file=None, llm_provider="ollama")
    assert ollama.llm_base_url == "http://127.0.0.1:11434/v1"
    assert ollama.model_name == "qwen2.5:7b"
    assert ollama.openai_api_key.get_secret_value() == "ollama"

    openrouter = Settings(_env_file=None, llm_provider="openrouter")
    assert openrouter.llm_base_url == "https://openrouter.ai/api/v1"
    assert openrouter.model_name == "openai/gpt-4o-mini"


def test_context_budgets_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            context_keep_recent_tokens=200_000,
            context_budget_tokens=100_000,
        )
