"""Backward-compatible import for code written before V11."""

from app.observability.recorder import TraceRecorder

__all__ = ["TraceRecorder"]
