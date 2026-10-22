"""Password strength policy: length is enforced by the schema, strength here (zxcvbn >= 3).

CPU heavy: async callers run ``check_password`` through ``anyio.to_thread.run_sync``.
"""

from collections.abc import Sequence

from zxcvbn import zxcvbn

from app.core.errors import DomainValidationError

MIN_SCORE = 3


def check_password(password: str, user_inputs: Sequence[str] = ()) -> None:
    inputs = [i for i in user_inputs if i]
    result = zxcvbn(password[:72], user_inputs=inputs)  # zxcvbn is quadratic on long input
    if int(result["score"]) < MIN_SCORE:
        raise DomainValidationError(
            "WEAK_PASSWORD", "Password is too easy to guess; choose a longer, less common one"
        )
