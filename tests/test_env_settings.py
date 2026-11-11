"""Provider and language writes to .env: validation, comment preservation, live update."""

from __future__ import annotations

import pytest

import monika.config as config


class FakeLive:
    def __init__(self) -> None:
        self.language = "fr"
        self.stt_language = "fr"
        self.llm_provider = "custom"
        self.llm_base_url = "https://old.example/v1"
        self.model_name = "old-model"


def test_provider_and_language_are_written_to_env(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "LANGUAGE=fr\n# LLM_PROVIDER=custom\nLLM_BASE_URL=https://old.example/v1\n",
        encoding="utf-8",
    )
    live = FakeLive()
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "get_settings", lambda: live)
    monkeypatch.setattr(config, "set_language", lambda code: None)

    config.write_env_settings(
        {
            "LANGUAGE": "en",
            "LLM_PROVIDER": "ollama",
            "LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "MODEL_NAME": "qwen2.5:7b",
        }
    )

    text = env.read_text(encoding="utf-8")
    assert "LANGUAGE=en" in text
    assert "# LLM_PROVIDER=custom" in text  # comments are never rewritten
    assert "LLM_PROVIDER=ollama" in text
    assert "LLM_BASE_URL=http://127.0.0.1:11434/v1" in text
    assert "MODEL_NAME=qwen2.5:7b" in text
    assert live.language == "en" and live.stt_language == "en"
    assert live.llm_provider == "ollama"
    assert live.model_name == "qwen2.5:7b"


def test_invalid_values_are_refused_before_writing(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", env)

    with pytest.raises(config.SettingsError):
        config.write_env_settings({"LLM_PROVIDER": "nope"})
    with pytest.raises(config.SettingsError):
        config.write_env_settings({"LLM_BASE_URL": "ftp://example"})
    with pytest.raises(config.SettingsError):
        config.write_env_settings({"MODEL_NAME": "  "})

    assert not env.exists()
