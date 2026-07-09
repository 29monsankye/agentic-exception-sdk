from _typeshed import Incomplete
from agentic_exception_sdk.bundle import ResilienceBundle

__all__ = ['BundleValidationError', 'validate_bundle']

class BundleValidationError(ValueError):
    failures: Incomplete
    report: Incomplete
    def __init__(self, failures: list[str]) -> None: ...

def validate_bundle(bundle: ResilienceBundle) -> None: ...
