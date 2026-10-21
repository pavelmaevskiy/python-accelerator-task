import pytest

from app.core.errors import DomainValidationError
from app.core.password_policy import check_password


@pytest.mark.parametrize("weak", ["passwordpassword", "123456789012", "qwertyuiopas"])
def test_common_passwords_are_rejected(weak: str) -> None:
    with pytest.raises(DomainValidationError) as e:
        check_password(weak)
    assert e.value.code == "WEAK_PASSWORD"


@pytest.mark.parametrize("strong", ["tr1cky-Horse-staple-9", "correct horse battery staple"])
def test_strong_passwords_are_accepted(strong: str) -> None:
    check_password(strong)


def test_password_built_from_user_inputs_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        check_password("annleeann", ["ann", "lee"])
