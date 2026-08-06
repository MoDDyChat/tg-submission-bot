"""Domain exceptions for the submission bot."""


class MSBotError(Exception):
    """Base exception for all bot domain errors."""


# ── Submission ─────────────────────────────────────────────────────

class SubmissionNotFoundError(MSBotError):
    def __init__(self, sub_id: int) -> None:
        self.sub_id = sub_id
        super().__init__(f"Submission {sub_id} not found")


class SubmissionCancelledError(MSBotError):
    def __init__(self, sub_id: int) -> None:
        self.sub_id = sub_id
        super().__init__(f"Submission {sub_id} was cancelled")


class SubmissionStatusError(MSBotError):
    def __init__(self, sub_id: int, status: str) -> None:
        self.sub_id = sub_id
        self.status = status
        super().__init__(f"Submission {sub_id} has unexpected status: {status}")


# ── Publication ────────────────────────────────────────────────────

class PublicationNotFoundError(MSBotError):
    def __init__(self, pub_id: int) -> None:
        self.pub_id = pub_id
        super().__init__(f"Publication {pub_id} not found")


class PublishFailedError(MSBotError):
    pass


class PublishStateUnknownError(MSBotError):
    def __init__(self, pub_id: int, sub_id: int) -> None:
        self.pub_id = pub_id
        self.sub_id = sub_id
        super().__init__(
            f"Publication {pub_id} for submission {sub_id} may be sent, but DB state could not be synchronized"
        )


