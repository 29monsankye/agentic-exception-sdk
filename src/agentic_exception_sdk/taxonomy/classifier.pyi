__all__ = ['ExceptionClassifier']

class ExceptionClassifier:
    def classify(self, exc: BaseException) -> _Classification: ...
