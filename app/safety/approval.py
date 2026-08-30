from __future__ import annotations

from pydantic import BaseModel


class Approval(BaseModel):
    approved: bool = False
    approver: str | None = None
    reason: str | None = None
