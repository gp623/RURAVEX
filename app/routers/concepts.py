"""Curriculum concepts and graph topology endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Dict

from app.database import get_db
from app.models import ConceptRecord, ConceptPrereq
from app.schemas import CurriculumGraphOut, ConceptOut

router = APIRouter(prefix="/concepts", tags=["concepts"])


@router.get("", response_model=CurriculumGraphOut)
def get_curriculum_graph(db: Session = Depends(get_db)):
    """Returns the complete 10-concept curriculum DAG including prerequisites and edges."""
    concepts_db = db.query(ConceptRecord).order_by(ConceptRecord.tier).all()
    edges_db = db.query(ConceptPrereq).all()

    # Map prerequisites per concept
    prereqs_map: Dict[str, List[str]] = {c.id: [] for c in concepts_db}
    for e in edges_db:
        if e.concept_id in prereqs_map:
            prereqs_map[e.concept_id].append(e.prereq_id)

    concepts_out = [
        ConceptOut(
            id=c.id,
            name=c.name,
            description=c.description,
            domain=c.domain,
            tier=c.tier,
            prerequisites=prereqs_map.get(c.id, []),
        )
        for c in concepts_db
    ]

    edges_out = [
        {"source": e.prereq_id, "target": e.concept_id}
        for e in edges_db
    ]

    return CurriculumGraphOut(concepts=concepts_out, edges=edges_out)
