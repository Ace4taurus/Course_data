from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sjtu_course_analysis.scheduler import Offering, Slot


@dataclass(frozen=True)
class Candidate:
    offering: Offering
    mask: int


@dataclass(frozen=True)
class BeamState:
    mask: int
    selected: tuple[Offering, ...]
    weighted_sum: float
    total_credits: float
    optional_count: int
    early_class_count: int


def search_best_timetable_beam(
    compulsory_by_course: dict[str, list[Offering]],
    optional_by_course: dict[str, list[Offering]],
    *,
    max_early_classes: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    beam_width: int = 500,
    per_course_limit: int = 30,
) -> tuple[list[Offering] | None, float | None, float | None, float | None, int]:
    if not compulsory_by_course and not optional_by_course:
        return [], 0.0, 0.0, None, 1

    beam_width = max(1, beam_width)
    per_course_limit = max(1, per_course_limit)
    compulsory_order = sorted(compulsory_by_course, key=lambda course_code: len(compulsory_by_course[course_code]))
    optional_order = sorted(optional_by_course, key=lambda course_code: len(optional_by_course[course_code]))
    course_items = [("compulsory", course_code) for course_code in compulsory_order]
    course_items.extend(("optional", course_code) for course_code in optional_order)

    limited_by_course = {
        course_code: offerings[:per_course_limit]
        for course_code, offerings in {**compulsory_by_course, **optional_by_course}.items()
    }
    all_offerings = [offering for offerings in limited_by_course.values() for offering in offerings]
    slot_to_bit = {slot: index for index, slot in enumerate(sorted({slot for offering in all_offerings for slot in offering.slots}))}
    candidates_by_course = {
        course_code: [Candidate(offering, make_mask(offering.slots, slot_to_bit)) for offering in offerings]
        for course_code, offerings in limited_by_course.items()
    }

    best_state: BeamState | None = None
    beam = [BeamState(0, (), 0.0, 0.0, 0, 0)]
    visited_nodes = 1

    for index, (course_kind, course_code) in enumerate(course_items):
        if progress_callback:
            progress_callback(index, len(course_items), course_code)
        next_beam: list[BeamState] = []
        for state in beam:
            if course_kind == "optional":
                next_beam.append(state)
                visited_nodes += 1
            for candidate in candidates_by_course[course_code]:
                if state.mask & candidate.mask:
                    continue
                early_class_count = state.early_class_count + int(candidate.offering.has_early_class)
                if max_early_classes is not None and early_class_count > max_early_classes:
                    continue
                offering = candidate.offering
                next_beam.append(
                    BeamState(
                        state.mask | candidate.mask,
                        (*state.selected, offering),
                        state.weighted_sum + offering.score * offering.credits,
                        state.total_credits + offering.credits,
                        state.optional_count + int(course_kind == "optional"),
                        early_class_count,
                    )
                )
                visited_nodes += 1
        if not next_beam:
            break
        next_beam.sort(key=state_key, reverse=True)
        beam = next_beam[:beam_width]
        layer_best = max((state for state in beam if compulsory_courses_selected(state, compulsory_by_course)), key=state_key, default=None)
        if layer_best is not None and (best_state is None or state_key(layer_best) > state_key(best_state)):
            best_state = layer_best

    for state in beam:
        if compulsory_courses_selected(state, compulsory_by_course) and (best_state is None or state_key(state) > state_key(best_state)):
            best_state = state

    if progress_callback:
        progress_callback(len(course_items), len(course_items), "done")
    if best_state is None or best_state.total_credits <= 0:
        return None, None, None, None, visited_nodes
    return list(best_state.selected), best_state.weighted_sum, best_state.total_credits, best_state.weighted_sum / best_state.total_credits, visited_nodes


def make_mask(slots: frozenset[Slot], slot_to_bit: dict[Slot, int]) -> int:
    mask = 0
    for slot in slots:
        mask |= 1 << slot_to_bit[slot]
    return mask


def state_key(state: BeamState) -> tuple[float, int, float, float, int]:
    weighted_average = state.weighted_sum / state.total_credits if state.total_credits else 0.0
    return (weighted_average, state.optional_count, state.total_credits, state.weighted_sum, -state.early_class_count)


def compulsory_courses_selected(state: BeamState, compulsory_by_course: dict[str, list[Offering]]) -> bool:
    selected_courses = {offering.course_code for offering in state.selected}
    return all(course_code in selected_courses for course_code in compulsory_by_course)
