import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.unit_of_work import UnitOfWork
from tests.fakes import FakeQueue


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def uow_factory(fake_queue):
    # No statement is ever executed, so no connection to the (unreachable) database is opened.
    engine = create_async_engine("postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none")

    def _make() -> UnitOfWork:
        return UnitOfWork(AsyncSession(engine), fake_queue)

    return _make


async def test_job_is_enqueued_only_after_commit_and_dropped_on_rollback(
    fake_queue, uow_factory
) -> None:
    uow = uow_factory()
    with pytest.raises(RuntimeError):
        async with uow:
            uow.enqueue("send_verification_email", user_id="u1", token="t")
            raise RuntimeError("boom")
    assert fake_queue.jobs == []
    async with uow_factory() as ok:
        ok.enqueue("send_verification_email", user_id="u1", token="t")
        assert fake_queue.jobs == []  # not yet
    assert [j.name for j in fake_queue.jobs] == ["send_verification_email"]
    assert fake_queue.jobs[0].kwargs == {"user_id": "u1", "token": "t"}


async def test_uow_is_reusable_and_does_not_leak_jobs_between_runs(fake_queue, uow_factory) -> None:
    uow = uow_factory()
    async with uow:
        uow.enqueue("a")
    async with uow:
        uow.enqueue("b")
    assert [j.name for j in fake_queue.jobs] == ["a", "b"]
