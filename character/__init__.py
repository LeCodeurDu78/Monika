"""Monika's inner life: emotions, relationship (favorability) and, later, a narrative journal.

Everything here is standard library only and independent of the agent loop; the loop talks to it through one
façade (see the roadmap). A failure in this package must never break a conversation turn.
"""

from character.models import ACTIVE_EMOTIONS, EMOTIONS, Appraisal, AvatarCue

__all__ = ["ACTIVE_EMOTIONS", "EMOTIONS", "Appraisal", "AvatarCue"]
