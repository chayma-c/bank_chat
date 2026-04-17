"""
Pydantic schemas — request bodies and response shapes for the /fraud/rules API.
"""

from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

RiskDomain = Literal["VELOCITY", "LIMIT", "GEOGRAPHIC", "AML", "BEHAVIORAL"]
Severity   = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class FraudRuleCreate(BaseModel):
    """Body for POST /fraud/rules"""
    name:          str        = Field(..., min_length=2, max_length=255)
    domain:        RiskDomain
    trigger:       str        = Field(..., min_length=3, max_length=512)
    triggerDetail: str        = Field("", max_length=512)
    points:        int        = Field(..., ge=0, le=100)
    severity:      Severity
    active:        bool       = True
    description:   str        = Field("", max_length=2000)

    @field_validator("points")
    @classmethod
    def points_in_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("points must be between 0 and 100")
        return v


class FraudRuleUpdate(BaseModel):
    """Body for PUT /fraud/rules/{id} — all fields optional"""
    name:          Optional[str]        = Field(None, min_length=2, max_length=255)
    domain:        Optional[RiskDomain] = None
    trigger:       Optional[str]        = Field(None, min_length=3, max_length=512)
    triggerDetail: Optional[str]        = Field(None, max_length=512)
    points:        Optional[int]        = Field(None, ge=0, le=100)
    severity:      Optional[Severity]   = None
    active:        Optional[bool]       = None
    description:   Optional[str]        = Field(None, max_length=2000)


class FraudRulePatch(BaseModel):
    """Body for PATCH /fraud/rules/{id} — lightweight toggle"""
    active: Optional[bool] = None


class FraudRuleResponse(BaseModel):
    """Response shape — matches Angular FraudRule interface exactly"""
    id:            str
    name:          str
    domain:        str
    trigger:       str
    triggerDetail: str
    points:        int
    severity:      str
    active:        bool
    description:   str
    createdAt:     Optional[datetime] = None
    updatedAt:     Optional[datetime] = None

    model_config = {"from_attributes": True}
