"""Locust scenarios for Epic-01. Not part of the pytest run; see README.md."""

import os
import random

from locust import HttpUser, between, task

PASSWORD = os.environ.get("LOAD_PASSWORD", "correct horse battery staple")
USERS = int(os.environ.get("LOAD_USERS", "50"))
ADMIN_TOKEN = os.environ.get("LOAD_ADMIN_TOKEN", "")


class LoginUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task(5)
    def login(self) -> None:
        email = f"load{random.randint(1, USERS)}@example.com"  # noqa: S311
        self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, name="login"
        )

    @task(1)
    def admin_directory(self) -> None:
        if ADMIN_TOKEN:
            self.client.get(
                "/api/v1/admin/users?limit=50",
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                name="admin users",
            )
