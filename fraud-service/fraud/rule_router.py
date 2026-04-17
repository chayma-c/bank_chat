"""
REST API router — /rules
(nginx strips /fraud/ before forwarding, so FastAPI sees /rules/...)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from fraud.database import get_db
from fraud.schemas  import FraudRuleCreate, FraudRuleUpdate, FraudRulePatch, FraudRuleResponse
from fraud import crud

router = APIRouter(prefix="/rules", tags=["Fraud Rules"])


# ── GET /rules ────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[FraudRuleResponse])
def list_rules(db: Session = Depends(get_db)):
    rules = crud.get_all_rules(db)
    return [r.to_dict() for r in rules]


# ── GET /rules/{id} ───────────────────────────────────────────────────────────
@router.get("/{rule_id}", response_model=FraudRuleResponse)
def get_rule(rule_id: str, db: Session = Depends(get_db)):
    rule = crud.get_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return rule.to_dict()


# ── POST /rules ───────────────────────────────────────────────────────────────
@router.post("/", response_model=FraudRuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(data: FraudRuleCreate, db: Session = Depends(get_db)):
    rule = crud.create_rule(db, data)
    return rule.to_dict()


# ── PUT /rules/{id} ───────────────────────────────────────────────────────────
@router.put("/{rule_id}", response_model=FraudRuleResponse)
def update_rule(rule_id: str, data: FraudRuleUpdate, db: Session = Depends(get_db)):
    rule = crud.update_rule(db, rule_id, data)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return rule.to_dict()


# ── PATCH /rules/{id} ─────────────────────────────────────────────────────────
@router.patch("/{rule_id}", response_model=FraudRuleResponse)
def patch_rule(rule_id: str, data: FraudRulePatch, db: Session = Depends(get_db)):
    if data.active is None:
        raise HTTPException(status_code=422, detail="'active' field is required for PATCH")
    rule = crud.patch_rule(db, rule_id, data.active)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return rule.to_dict()


# ── DELETE /rules/{id} ────────────────────────────────────────────────────────
@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, db: Session = Depends(get_db)):
    deleted = crud.delete_rule(db, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")