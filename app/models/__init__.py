"""Import every model module so ``Base.metadata`` is complete (Alembic, tests)."""

from app.models import admin, core, identity  # noqa: F401
