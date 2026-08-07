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


# ── Config ─────────────────────────────────────────────────────────

class MessagesConfigError(MSBotError):
    """Raised at startup when config/messages.yaml is malformed or drifts
    from the expected shape (unknown key, wrong type, mismatched placeholders).
    Fails loud at load time so a bad edit never surfaces as a runtime
    ``KeyError`` inside a handler's ``.format()`` call."""


# ── Roles ──────────────────────────────────────────────────────────

class RoleChangeError(MSBotError):
    """Base error for role-change invariant violations."""


class CannotChangeOwnRoleError(RoleChangeError):
    """Actor tried to grant or revoke a role on themselves."""


class CannotRemoveLastAdminError(RoleChangeError):
    """Tried to revoke admin rights from the last remaining admin."""


class ConfigProtectedRoleError(RoleChangeError):
    """Tried to remove a role the target has via MODERATOR_IDS/ADMIN_IDS."""


class RoleAlreadyGrantedError(RoleChangeError):
    """Tried to grant a role the target already holds."""


class RoleTargetBannedError(RoleChangeError):
    """Tried to grant a role to a banned user."""


# ── Ban ────────────────────────────────────────────────────────────

class CannotBanModeratorError(MSBotError):
    """Tried to ban a user who still holds a moderator or admin role."""


