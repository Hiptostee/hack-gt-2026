class AppError(Exception):
    """User-visible errors shared by module and python -m entry points."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
