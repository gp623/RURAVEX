"""Curriculum DAG and Knowledge Graph Inference for MasteryFlow.
Pure Python, no framework imports.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple
from collections import deque
from engine.types import Concept, MasteryState, MasteryStatus
from engine.params import EngineConfig, DEFAULT_CONFIG

# Default 10 concepts for the Fractions -> Ratios -> Percentages curriculum
DEFAULT_CONCEPTS: List[Concept] = [
    Concept(
        id="fraction_basics",
        name="Fraction Basics",
        description="Understanding numerators, denominators, and parts of a whole.",
        domain="fractions",
        tier=1,
        prerequisites=[],
    ),
    Concept(
        id="equivalent_fractions",
        name="Equivalent Fractions",
        description="Finding and simplifying fractions that represent the same value.",
        domain="fractions",
        tier=2,
        prerequisites=["fraction_basics"],
    ),
    Concept(
        id="comparing_fractions",
        name="Comparing Fractions",
        description="Comparing and ordering fractions with like and unlike denominators.",
        domain="fractions",
        tier=3,
        prerequisites=["equivalent_fractions"],
    ),
    Concept(
        id="fraction_operations",
        name="Fraction Operations",
        description="Addition, subtraction, multiplication, and division of fractions.",
        domain="fractions",
        tier=3,
        prerequisites=["equivalent_fractions"],
    ),
    Concept(
        id="decimals_basics",
        name="Decimals Basics",
        description="Place value of decimals, converting terminating decimals to fractions.",
        domain="decimals",
        tier=1,
        prerequisites=[],
    ),
    Concept(
        id="ratios",
        name="Ratios & Rates",
        description="Part-to-part and part-to-whole relationships, language of ratios.",
        domain="ratios",
        tier=4,
        prerequisites=["comparing_fractions", "fraction_operations"],
    ),
    Concept(
        id="unit_rates",
        name="Unit Rates",
        description="Calculating constant of proportionality and price/speed per single unit.",
        domain="ratios",
        tier=5,
        prerequisites=["ratios"],
    ),
    Concept(
        id="proportions",
        name="Proportions",
        description="Setting up and solving proportional equations and scaling factors.",
        domain="ratios",
        tier=6,
        prerequisites=["unit_rates"],
    ),
    Concept(
        id="percentages",
        name="Percentages Basics",
        description="Expressing fractions and decimals as hundredths (percent).",
        domain="percentages",
        tier=7,
        prerequisites=["proportions", "decimals_basics"],
    ),
    Concept(
        id="percentage_change",
        name="Percentage Change",
        description="Calculating markups, discounts, percent increase and decrease.",
        domain="percentages",
        tier=8,
        prerequisites=["percentages"],
    ),
]


class CurriculumGraph:
    """Manages the curriculum DAG, topological relationships, prerequisite checking,
    and Bayesian graph inference for cold-start diagnostic.
    """

    def __init__(self, concepts: Optional[List[Concept]] = None):
        self.concepts: Dict[str, Concept] = {}
        self.adj_parents: Dict[str, List[str]] = {}   # concept -> list of prerequisites (parents)
        self.adj_children: Dict[str, List[str]] = {}  # concept -> list of dependents (children)

        source_concepts = concepts or DEFAULT_CONCEPTS
        for c in source_concepts:
            self.concepts[c.id] = c
            self.adj_parents[c.id] = list(c.prerequisites)
            self.adj_children[c.id] = []

        for c_id, prereqs in self.adj_parents.items():
            for p_id in prereqs:
                if p_id in self.adj_children:
                    self.adj_children[p_id].append(c_id)

    def get_concept(self, concept_id: str) -> Optional[Concept]:
        return self.concepts.get(concept_id)

    def get_prerequisites(self, concept_id: str) -> List[str]:
        return self.adj_parents.get(concept_id, [])

    def get_ancestors(self, concept_id: str) -> Set[str]:
        """Returns all transitive ancestors (prerequisites) in the DAG."""
        ancestors: Set[str] = set()
        queue = deque(self.get_prerequisites(concept_id))
        while queue:
            curr = queue.popleft()
            if curr not in ancestors:
                ancestors.add(curr)
                queue.extend(self.get_prerequisites(curr))
        return ancestors

    def get_descendants(self, concept_id: str) -> Set[str]:
        """Returns all transitive descendants in the DAG."""
        descendants: Set[str] = set()
        queue = deque(self.adj_children.get(concept_id, []))
        while queue:
            curr = queue.popleft()
            if curr not in descendants:
                descendants.add(curr)
                queue.extend(self.adj_children.get(curr, []))
        return descendants

    def check_prerequisites(
        self,
        concept_id: str,
        student_states: Dict[str, MasteryState],
        config: EngineConfig = DEFAULT_CONFIG,
    ) -> Tuple[bool, Optional[str], List[Tuple[str, float]]]:
        """Validates if student has mastered prerequisites for `concept_id`.
        Returns:
            - is_ready (bool): True if all ancestors meet readiness threshold.
            - weakest_ancestor_id (Optional[str]): The ancestor with lowest p_mastery.
            - unsatisfied_ancestors (List[Tuple[str, float]]): [(ancestor_id, p_mastery), ...]
        """
        ancestors = self.get_ancestors(concept_id)
        if not ancestors:
            return True, None, []

        unsatisfied: List[Tuple[str, float]] = []
        for anc_id in ancestors:
            state = student_states.get(anc_id)
            p = state.p_mastery if state else 0.15
            if p < config.prereq_readiness_threshold:
                unsatisfied.append((anc_id, p))

        if not unsatisfied:
            return True, None, []

        # Sort unsatisfied ancestors by lowest p_mastery first (weakest ancestor)
        unsatisfied.sort(key=lambda item: item[1])
        weakest_ancestor = unsatisfied[0][0]
        return False, weakest_ancestor, unsatisfied

    def infer_cold_start_states(
        self,
        student_id: str,
        diagnostic_evidence: Dict[str, Tuple[bool, float]],  # concept_id -> (passed, score_p)
        config: EngineConfig = DEFAULT_CONFIG,
    ) -> Dict[str, MasteryState]:
        """Uses graph inference to initialize all 10 concepts from 6 diagnostic answers.
        Nobody starts at zero. If leaf concept is mastered, ancestors are inferred
        proficient. If root is failed, descendants are inferred unlearned.
        """
        states: Dict[str, MasteryState] = {}

        # 1. Initialize observed concepts
        for c_id, concept in self.concepts.items():
            if c_id in diagnostic_evidence:
                passed, score = diagnostic_evidence[c_id]
                p = max(0.15, min(0.92, score if passed else max(0.20, score * 0.4)))
                u = 0.25  # Measured directly, lower uncertainty
                has_transfer = passed and concept.tier >= 3
                status = MasteryStatus.PROFICIENT if p >= config.mastery_threshold else MasteryStatus.LEARNING
                states[c_id] = MasteryState(
                    student_id=student_id,
                    concept_id=c_id,
                    p_mastery=round(p, 4),
                    uncertainty=round(u, 4),
                    has_passed_transfer=has_transfer,
                    highest_difficulty_passed=3 if passed else 1,
                    status=status,
                    total_attempts=1,
                    recent_history=[passed],
                )

        # 2. Graph propagation for unmeasured concepts
        for c_id, concept in self.concepts.items():
            if c_id in states:
                continue

            # Ancestor signals (descendants of c_id that were measured)
            descendants = self.get_descendants(c_id)
            measured_desc = [states[d] for d in descendants if d in states]

            # Prerequisite signals (ancestors of c_id that were measured)
            ancestors = self.get_ancestors(c_id)
            measured_anc = [states[a] for a in ancestors if a in states]

            if measured_desc and any(d.p_mastery >= config.mastery_threshold for d in measured_desc):
                # Strong upward evidence: Student passed child, ancestor must be understood
                max_desc_p = max(d.p_mastery for d in measured_desc)
                inferred_p = max(0.68, max_desc_p * 0.90)
                inferred_u = 0.40
                status = MasteryStatus.PROFICIENT if inferred_p >= config.mastery_threshold else MasteryStatus.LEARNING
            elif measured_anc and any(a.p_mastery < 0.40 for a in measured_anc):
                # Strong downward evidence: Student failed parent, unlikely to know child
                min_anc_p = min(a.p_mastery for a in measured_anc)
                inferred_p = max(0.15, min_anc_p * 0.75)
                inferred_u = 0.45
                status = MasteryStatus.STRUGGLING if inferred_p < 0.25 else MasteryStatus.LEARNING
            elif measured_anc:
                # Average of measured ancestors
                avg_anc = sum(a.p_mastery for a in measured_anc) / len(measured_anc)
                inferred_p = max(0.20, avg_anc * 0.80)
                inferred_u = 0.50
                status = MasteryStatus.LEARNING
            else:
                # Baseline default prior: nobody starts at zero
                inferred_p = 0.30
                inferred_u = 0.70
                status = MasteryStatus.UNENCOUNTERED

            states[c_id] = MasteryState(
                student_id=student_id,
                concept_id=c_id,
                p_mastery=round(inferred_p, 4),
                uncertainty=round(inferred_u, 4),
                has_passed_transfer=False,
                highest_difficulty_passed=0,
                status=status,
                total_attempts=0,
                recent_history=[],
            )

        return states
