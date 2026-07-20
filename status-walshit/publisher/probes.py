"""Typed, local-only contract for caller-injected probe outcomes."""

from dataclasses import dataclass

from publisher.schema import COMPONENTS
from publisher.statemachine import PROBE_OUTCOMES


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One deliberately minimal probe outcome supplied by a caller."""

    component_id: str
    outcome: str

    def __post_init__(self):
        if type(self.component_id) is not str:
            raise TypeError("component_id must be a string")
        if self.component_id not in COMPONENTS:
            raise ValueError("component_id must be a public component")
        if type(self.outcome) is not str:
            raise TypeError("outcome must be a string")
        if self.outcome not in PROBE_OUTCOMES:
            raise ValueError("outcome must be a supported probe outcome")


def validate_complete_cycle(results) -> tuple[ProbeResult, ...]:
    """Validate one result per component and return canonical registry order."""
    if type(results) not in (list, tuple):
        raise TypeError("probe results must be a concrete sequence")
    if any(type(result) is not ProbeResult for result in results):
        raise TypeError("every probe result must be a ProbeResult")
    component_ids = [result.component_id for result in results]
    if len(component_ids) != len(set(component_ids)):
        raise ValueError("probe results contain duplicate components")
    if set(component_ids) != set(COMPONENTS):
        raise ValueError("probe results must contain every component exactly once")
    by_component = {result.component_id: result for result in results}
    return tuple(by_component[component_id] for component_id in COMPONENTS)
