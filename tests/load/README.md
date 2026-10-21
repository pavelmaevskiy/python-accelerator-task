# Load tests (not run in CI)

Targets from `tasks/TASK-001/brainstorm-design.md` "Testing Strategy":

| Scenario | Target |
|----------|--------|
| `POST /auth/login` | p95 < 300 ms at 50 RPS |
| ShareLink registration, 200 concurrent redemptions of one static link | 0 lost `use_count` writes (covered functionally by `tests/integration/test_register_concurrency.py`) |
| `GET /admin/users` over 10k users | p95 < 500 ms, index scan (plan asserted in `tests/integration/test_admin_users_explain.py`) |

Run against a seeded server (locust is intentionally not a project dependency):

```bash
docker compose up -d db redis
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory &
LOAD_USERS=200 uvx locust -f tests/load/locustfile.py --host http://localhost:8000 \
    --headless -u 50 -r 10 -t 1m
```

`LOAD_ADMIN_TOKEN` (a super-admin access token) enables the admin-directory task.
