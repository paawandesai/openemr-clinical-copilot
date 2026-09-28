"""
GET /version (app/version.py). No network.

    cd agent && python -m pytest tests/test_version.py
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import version

SHA = "f0040f8a1b2c3d4e5f60718293a4b5c6d7e8f901"


def _get(env: dict[str, str]) -> dict[str, object]:
    app = FastAPI()
    app.include_router(version.router)
    with mock.patch.dict(os.environ, env, clear=False):
        for name in ("AGENT_GIT_COMMIT", "AGENT_BUILT_AT"):
            if name not in env:
                os.environ.pop(name, None)
        response = TestClient(app).get("/version")
    assert response.status_code == 200
    return response.json()


class VersionTests(unittest.TestCase):
    def test_reports_the_stamped_commit_and_build_time(self) -> None:
        body = _get({"AGENT_GIT_COMMIT": SHA, "AGENT_BUILT_AT": "2026-09-28T16:00:00Z"})
        self.assertEqual(body, {"commit": SHA, "built_at": "2026-09-28T16:00:00Z"})

    def test_an_unstamped_build_says_unknown_rather_than_guessing(self) -> None:
        self.assertEqual(_get({}), {"commit": "unknown", "built_at": None})

    def test_an_abbreviated_sha_is_accepted_and_case_folded(self) -> None:
        self.assertEqual(_get({"AGENT_GIT_COMMIT": "F0040F8"})["commit"], "f0040f8")

    def test_a_value_that_is_not_a_sha_is_never_echoed(self) -> None:
        for bad in ("unknown", "main", "f0040f8; rm -rf /", "<script>", SHA + "0", "abc12"):
            with self.subTest(bad=bad):
                self.assertEqual(_get({"AGENT_GIT_COMMIT": bad})["commit"], "unknown")

    def test_a_build_time_that_is_not_iso_8601_is_null(self) -> None:
        self.assertIsNone(_get({"AGENT_BUILT_AT": "yesterday"})["built_at"])

    def test_the_route_is_mounted_on_the_service(self) -> None:
        from app.main import app

        with mock.patch.dict(os.environ, {"AGENT_GIT_COMMIT": SHA}):
            response = TestClient(app).get("/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["commit"], SHA)


if __name__ == "__main__":
    unittest.main()
