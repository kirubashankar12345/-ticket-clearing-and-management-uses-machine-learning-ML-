class TicketCreationError(Exception):
    pass


class ConfirmationRequiredError(Exception):
    """Raised when attempting to set status=Resolved without employee confirmation."""
    pass


class ReopenWindowExpiredError(Exception):
    """Raised when the 7-day reopen window has passed."""
    pass


class EmailAlreadyExistsError(Exception):
    pass


class AIServiceUnavailableError(Exception):
    pass


class TicketOwnershipError(Exception):
    pass
