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


def test_context_budgets_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            context_keep_recent_tokens=200_000,
            context_budget_tokens=100_000,
        )
