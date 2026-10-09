from uuid import UUID

from pydantic import ValidationError


class InvalidCheckpointError(Exception):
    def __init__(self, command_id: UUID, validation_error: ValidationError) -> None:
        self.command_id = command_id
        self.validation_error = validation_error
        self.summary = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg', 'invalid')}"
            for item in validation_error.errors()
        )
        super().__init__(f"Command {command_id} has an invalid checkpoint: {self.summary}")


class ConflictError(ValueError):
    """An optimistic revision check failed; canonical state was not changed."""


class DraftRevisionError(Exception):
    """An invalid edit list remains after bounded repair; the draft is intact."""


class StoryUnavailableError(ValueError):
    """The story is in the recycle bin; ordinary reads and writes are unavailable."""
