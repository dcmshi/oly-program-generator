# oly-agent/tests/test_web_routers.py
"""
FastAPI route tests using TestClient with mocked DB and query functions.

Authentication uses a properly signed session cookie (same as real sessions)
so the auth middleware works correctly without patching internals.
All tests are mock-based — no live DB or API keys required.

Run: python tests/test_web_routers.py
"""

import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

RESULTS = []
_INTEGRATION = os.getenv("INTEGRATION_TESTS", "").lower() in ("1", "true")


class _Skip(Exception):
    pass


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except _Skip as e:
        RESULTS.append(("SKIP", name, str(e)))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _integration_only():
    if not _INTEGRATION:
        raise _Skip("set INTEGRATION_TESTS=1 to enable")


# ── App + auth setup ──────────────────────────────────────────────────────────

from web.app import app
from web.deps import get_db, get_settings

_mock_conn = MagicMock()


def _db_override():
    yield _mock_conn


app.dependency_overrides[get_db] = _db_override


def _make_session_cookie(data: dict) -> str:
    """Create a properly signed Starlette session cookie."""
    from itsdangerous import TimestampSigner
    secret = get_settings().secret_key
    signer = TimestampSigner(secret)
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    return signer.sign(payload).decode("utf-8")


_AUTH_COOKIE = _make_session_cookie({"athlete_id": 1, "athlete_name": "Test"})

# Default client: authenticated (has a valid session cookie)
_client = TestClient(app, follow_redirects=True)
_client.cookies.set("session", _AUTH_COOKIE)

# Unauthenticated client: no session, no redirect following
_unauthed = TestClient(app, follow_redirects=False)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _program(status="active", **overrides):
    base = {
        "id": 1, "name": "Accumulation Block", "status": status,
        "phase": "accumulation", "duration_weeks": 6, "sessions_per_week": 4,
        "start_date": "2026-01-01", "rationale": None, "outcome_summary": None,
    }
    return {**base, **overrides}


def _week_data():
    return [{
        "week_number": 1,
        "sessions": [{
            "id": 1, "week_number": 1, "day_number": 1,
            "session_label": "Snatch Day", "estimated_duration_minutes": 60,
            "focus_area": "snatch", "log_id": None,
            "exercises": [{
                "exercise_order": 1, "exercise_name": "Snatch",
                "sets": 4, "reps": 3, "intensity_pct": 75.0,
                "absolute_weight_kg": 75.0, "rpe_target": 7.5,
                "rest_seconds": 180, "selection_rationale": None,
            }],
        }],
    }]


def _session_detail():
    return {
        "id": 1, "week_number": 1, "day_number": 1,
        "session_label": "Snatch Day", "estimated_duration_minutes": 60,
        "focus_area": "snatch", "program_id": 1,
        "exercises": [{
            "id": 1, "exercise_order": 1, "exercise_name": "Snatch",
            "sets": 4, "reps": 3, "intensity_pct": 75.0,
            "absolute_weight_kg": 75.0, "rpe_target": 7.5,
            "rest_seconds": 180, "selection_rationale": None,
        }],
    }


def _log():
    return {
        "id": 10, "session_id": 1, "athlete_id": 1,
        "log_date": "2026-01-01", "overall_rpe": 7.0, "notes": None,
        "session_feel": None,
    }


def _tle():
    return {
        "id": 5, "exercise_name": "Snatch", "weight_kg": 75.0,
        "rpe": 7.0, "reps_per_set": [3, 3, 3], "sets_completed": 3,
        "make_rate": 0.9, "technical_notes": None, "session_exercise_id": 1,
    }


# ── Auth / middleware tests ───────────────────────────────────────────────────

def test_unauthenticated_redirect_to_login():
    """Requests without a session cookie should redirect to /login."""
    r = _unauthed.get("/")
    assert r.status_code in (302, 307), f"Expected redirect, got {r.status_code}"
    assert "/login" in r.headers.get("location", "").lower()


def test_login_page_accessible_without_auth():
    """GET /login should be publicly accessible."""
    r = _unauthed.get("/login")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_login_invalid_credentials_returns_401():
    with patch("web.routers.auth.fetch_one", return_value=None):
        r = _unauthed.post("/login", data={"username": "bad", "password": "bad"})
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


def test_login_valid_credentials_redirects():
    import bcrypt
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    mock_athlete = {"id": 1, "name": "Test", "password_hash": hashed}
    client = TestClient(app, follow_redirects=False)
    with patch("web.routers.auth.fetch_one", return_value=mock_athlete):
        r = client.post("/login", data={"username": "test", "password": "secret"})
    assert r.status_code == 303, f"Expected 303, got {r.status_code}"
    assert r.headers.get("location") == "/"


def test_htmx_unauthenticated_returns_hx_redirect():
    """HTMX requests from unauthenticated users get HX-Redirect, not 302."""
    r = _unauthed.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200, f"Expected 200 (HX-Redirect), got {r.status_code}"
    assert r.headers.get("HX-Redirect") == "/login"


# ── Dashboard ─────────────────────────────────────────────────────────────────

def test_dashboard_no_program():
    with patch("web.queries.dashboard.get_active_program", return_value=None):
        with patch("web.queries.program.get_athlete_maxes", return_value=[]):
            with patch("web.queries.dashboard.get_lift_ratios", return_value={}):
                r = _client.get("/")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"No program" in r.content or b"Generate" in r.content, "Expected no-program state"


def test_dashboard_with_active_program():
    prog = _program()
    with patch("web.queries.dashboard.get_active_program", return_value=prog):
        with patch("web.queries.dashboard.get_current_week_sessions", return_value=[]):
            with patch("web.queries.dashboard.get_adherence", return_value={"prescribed": 0, "logged": 0, "pct": 0}):
                with patch("web.queries.dashboard.get_warnings", return_value=[]):
                    with patch("web.queries.program.get_athlete_maxes", return_value=[]):
                        with patch("web.queries.dashboard.get_lift_ratios", return_value={}):
                            r = _client.get("/")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"Accumulation Block" in r.content


# ── Program list ──────────────────────────────────────────────────────────────

def test_program_list_empty():
    with patch("web.queries.program.get_all_programs", return_value=[]):
        r = _client.get("/program")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"No programs" in r.content or b"Generate" in r.content


def test_program_list_with_programs():
    with patch("web.queries.program.get_all_programs", return_value=[_program()]):
        r = _client.get("/program")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"Accumulation Block" in r.content


# ── Program detail ────────────────────────────────────────────────────────────

def test_program_detail_renders():
    with patch("web.queries.program.get_program", return_value=_program()):
        with patch("web.queries.program.get_program_weeks", return_value=_week_data()):
            with patch("web.queries.program.get_program_volume_by_week", return_value=[]):
                r = _client.get("/program/1")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"Accumulation Block" in r.content


def test_program_detail_404_for_missing():
    with patch("web.queries.program.get_program", return_value=None):
        r = _client.get("/program/999")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


def test_program_activate():
    with patch("web.queries.program.activate_program", return_value=None):
        with patch("web.queries.program.get_program", return_value=_program(status="active")):
            r = _client.post("/program/1/activate")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_program_abandon():
    with patch("web.queries.program.abandon_program", return_value=None):
        r = _client.post("/program/1/abandon")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


# ── Session log ───────────────────────────────────────────────────────────────

def test_log_form_renders():
    with patch("web.queries.log_session.get_session_with_exercises", return_value=_session_detail()):
        with patch("web.queries.log_session.get_existing_log", return_value=None):
            r = _client.get("/log/1")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert b"Snatch Day" in r.content


def test_log_form_404_for_missing_session():
    with patch("web.queries.log_session.get_session_with_exercises", return_value=None):
        r = _client.get("/log/999")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


def test_log_form_shows_existing_log():
    with patch("web.queries.log_session.get_session_with_exercises", return_value=_session_detail()):
        with patch("web.queries.log_session.get_existing_log", return_value=_log()):
            with patch("web.queries.log_session.get_logged_exercises", return_value=[_tle()]):
                r = _client.get("/log/1")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_submit_session_log_creates_new_log():
    with patch("web.queries.log_session.get_session_with_exercises", return_value=_session_detail()):
        with patch("web.queries.log_session.get_existing_log", side_effect=[None, _log()]):
            with patch("web.queries.log_session.create_session_log", return_value=10):
                with patch("web.queries.log_session.get_logged_exercises", return_value=[]):
                    r = _client.post("/log/1", data={"overall_rpe": "7", "notes": ""})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_submit_session_log_updates_existing():
    with patch("web.queries.log_session.get_session_with_exercises", return_value=_session_detail()):
        with patch("web.queries.log_session.get_existing_log", side_effect=[_log(), _log()]):
            with patch("web.queries.log_session.update_session_log", return_value=None):
                with patch("web.queries.log_session.get_logged_exercises", return_value=[]):
                    r = _client.post("/log/1", data={"overall_rpe": "8", "notes": ""})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_delete_exercise_log():
    with patch("web.queries.log_session.delete_exercise_log", return_value=None):
        with patch("web.queries.log_session.get_log_by_id", return_value=_log()):
            with patch("web.queries.log_session.get_session_with_exercises", return_value=_session_detail()):
                with patch("web.queries.log_session.get_logged_exercises", return_value=[]):
                    r = _client.delete("/log/10/exercise/5")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_update_exercise_log():
    with patch("web.queries.log_session.update_exercise_log", return_value=None):
        with patch("web.queries.log_session.get_exercise_log_entry", return_value=_tle()):
            with patch("web.queries.log_session.get_log_by_id", return_value=_log()):
                with patch("web.queries.log_session.maybe_promote_max", return_value=False):
                    r = _client.post(
                        "/log/10/exercise/5",
                        data={"weight_kg": "80", "rpe": "7.5", "sets_completed": "3", "reps_per_set": "3,3,3"},
                    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


# ── Maxes ─────────────────────────────────────────────────────────────────────

def test_update_max_success():
    with patch("web.queries.program.upsert_athlete_max", return_value=(False, 80.0)):
        with patch("web.queries.program.get_athlete_maxes", return_value=[]):
            r = _client.post("/program/maxes/update", data={"exercise_name": "Snatch", "weight_kg": "85"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    skipped = sum(1 for r in RESULTS if r[0] == "SKIP")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
