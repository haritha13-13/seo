"""Domain errors. The web layer maps these to messages; services never touch HTTP."""


class DomainError(Exception):
    """Base class; str(error) is safe to show the operator."""


class NotFound(DomainError):
    pass


class Invalid(DomainError):
    pass


class Duplicate(DomainError):
    pass


class InUse(DomainError):
    """Cannot hard-delete a record that has Runs; deactivate it instead."""


class RunAlreadyActive(DomainError):
    pass
