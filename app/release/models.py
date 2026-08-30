from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ThreatControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    threat_id: str
    threat: str
    attack_example: str
    control_layers: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    status: Literal["implemented_and_tested"] = "implemented_and_tested"
    boundary: str


class EvidenceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: Literal["code", "test", "document", "report"]
    sha256: str
    size_bytes: int


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release: str
    generated_at: datetime
    algorithm: Literal["sha256"] = "sha256"
    entries: tuple[EvidenceEntry, ...]


class ReleaseReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release: str
    scope: Literal["interview_final"] = "interview_final"
    status: Literal["interview_ready", "needs_validation"]
    feature_freeze: bool
    core_gate: dict[str, object]
    reliability_gate: dict[str, object] = Field(default_factory=dict)
    operational_gate: dict[str, object] = Field(default_factory=dict)
    visual_gate: dict[str, object] = Field(default_factory=dict)
    quality_metrics: dict[str, object] = Field(default_factory=dict)
    run_bundle: dict[str, object] = Field(default_factory=dict)
    threat_coverage: dict[str, object]
    evidence_integrity: dict[str, object]
    external_integrations: dict[str, object]
    production_readiness: dict[str, object]
