"""
REST API router — /rules
(nginx strips /fraud/ before forwarding, so FastAPI sees /rules/...)
"""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from fraud.database import get_db
from fraud.schemas  import FraudRuleCreate, FraudRuleUpdate, FraudRulePatch, FraudRuleResponse
from fraud.auth     import require_bank_agent, require_admin
from fraud import crud
from fraud.llm_rule_classifier import invalidate_rule_cache

_DUPLICATE_MSG = (
    "A rule with the same semantic intent already exists: '{name}' ({rule_id}). "
    "Modify the existing rule instead of creating a duplicate."
)

router = APIRouter(prefix="/rules", tags=["Fraud Rules"])


def _actor(payload: dict) -> str:
    return payload.get("preferred_username") or payload.get("sub", "unknown")


# ── GET /rules ────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[FraudRuleResponse])
def list_rules(
    db: Session = Depends(get_db),
    _: dict = Depends(require_bank_agent),
):
    rules = crud.get_all_rules(db)
    return [r.to_dict() for r in rules]


# ── GET /rules/{id} ───────────────────────────────────────────────────────────
@router.get("/{rule_id}", response_model=FraudRuleResponse)
def get_rule(
    rule_id: Annotated[str, Path(pattern=r"^RL-[A-Z]{1,3}-[A-Z0-9]{3,6}$")],
    db: Session = Depends(get_db),
    _: dict = Depends(require_bank_agent),
):
    rule = crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return rule.to_dict()


# ── POST /rules ───────────────────────────────────────────────────────────────
@router.post("/", response_model=FraudRuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(
    data: FraudRuleCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    conflict = crud.find_duplicate_rule(
        db,
        trigger=data.trigger,
        domain=data.domain,
        name=data.name,
        trigger_detail=data.triggerDetail,
        description=data.description,
    )
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=_DUPLICATE_MSG.format(name=conflict.name, rule_id=conflict.id),
        )
    rule = crud.create_rule(db, data, actor=_actor(current_user))
    return rule.to_dict()


# ── PUT /rules/{id} ───────────────────────────────────────────────────────────
@router.put("/{rule_id}", response_model=FraudRuleResponse)
def update_rule(
    rule_id: Annotated[str, Path(pattern=r"^RL-[A-Z]{1,3}-[A-Z0-9]{3,6}$")],
    data: FraudRuleUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    # Only check for duplicates when trigger or domain is being changed
    if data.trigger is not None or data.domain is not None:
        current = crud.get_rule(db, rule_id)
        if current:
            check_trigger = data.trigger if data.trigger is not None else current.trigger
            check_domain  = data.domain  if data.domain  is not None else current.domain
            conflict = crud.find_duplicate_rule(
                db,
                trigger=check_trigger,
                domain=check_domain,
                name=data.name or current.name,
                trigger_detail=data.triggerDetail if data.triggerDetail is not None else (current.trigger_detail or ""),
                description=data.description or current.description or "",
                exclude_id=rule_id,
            )
            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=_DUPLICATE_MSG.format(name=conflict.name, rule_id=conflict.id),
                )
    rule = crud.update_rule(db, rule_id, data, actor=_actor(current_user))
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    invalidate_rule_cache(rule_id)
    return rule.to_dict()


# ── PATCH /rules/{id} ─────────────────────────────────────────────────────────
@router.patch("/{rule_id}", response_model=FraudRuleResponse)
def patch_rule(
    rule_id: Annotated[str, Path(pattern=r"^RL-[A-Z]{1,3}-[A-Z0-9]{3,6}$")],
    data: FraudRulePatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    if data.active is None:
        raise HTTPException(status_code=422, detail="'active' field is required for PATCH")
    rule = crud.patch_rule(db, rule_id, data.active, actor=_actor(current_user))
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    invalidate_rule_cache(rule_id)
    return rule.to_dict()


# ── DELETE /rules/{id} ────────────────────────────────────────────────────────
@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: Annotated[str, Path(pattern=r"^RL-[A-Z]{1,3}-[A-Z0-9]{3,6}$")],
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    deleted = crud.delete_rule(db, rule_id, actor=_actor(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    invalidate_rule_cache(rule_id)