"""Bounded cache of CEL programs, independent of request and account bindings."""

from functools import lru_cache

import celpy


@lru_cache(maxsize=256)
def compile_cel_program(expression: str) -> celpy.Runner:
    """Compile an expression once for validation and subsequent evaluations.

    Each evaluation creates its own activation; request values never enter
    this cache. Eviction bounds memory when accounts edit their policies.
    """
    environment = celpy.Environment()
    return environment.program(environment.compile(expression))
