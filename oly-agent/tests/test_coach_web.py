"""COACH-2: a coach account works on linked athletes — the acting dependency,
the /coach routes, login-detail guards and the coaching banner."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from tests.test_web_routers import _full_athlete, _make_session_cookie, _render_base, app

ACTING = {"athlete_id": 1, "athlete_name": "Paul", "acting_athlete_id": 7, "acting_athlete_name": "Sam"}


def _client(session: dict, follow=True) -> TestClient:
    c = TestClient(app, follow_redirects=follow)
    c.cookies.set("session", _make_session_cookie(session))
    return c


# ── The dependency ───────────────────────────────────────────────────────────

def _resolve(session: dict, linked: bool) -> int:
    from web.auth import get_current_athlete_id
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=linked)):
        return asyncio.run(get_current_athlete_id(SimpleNamespace(session=session), conn=object()))


def test_acting_resolves_to_the_coached_athlete_only_while_linked():
    assert _resolve({"athlete_id": 1}, linked=False) == 1                  # not coaching: no DB check
    session = dict(ACTING)
    assert _resolve(session, linked=True) == 7
    assert _resolve(session, linked=False) == 1                            # link revoked mid-session
    assert "acting_athlete_id" not in session and "acting_athlete_name" not in session


def test_every_athlete_page_reads_the_coached_athlete():
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
         patch("web.queries.profile.get_athlete", new=AsyncMock(return_value=_full_athlete())) as get_athlete, \
         patch("web.queries.profile.get_active_goal", new=AsyncMock(return_value=None)):
        r = _client(ACTING).get("/profile")
    assert r.status_code == 200
    get_athlete.assert_awaited_with(get_athlete.await_args.args[0], 7)
    assert "You are coaching" in r.text and 'action="/profile/password"' not in r.text


# ── Login details stay with the account ─────────────────────────────────────

def test_password_and_username_changes_are_refused_while_coaching():
    for path, form in (("/profile/password", {"current_password": "x", "new_password": "longenough1",
                                              "confirm_password": "longenough1"}),
                       ("/profile/username", {"new_username": "someone", "confirm_password_u": "x"})):
        with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
             patch("web.queries.profile.update_password", new=AsyncMock()) as upd:
            r = _client(ACTING, follow=False).post(path, data=form)
        assert r.status_code == 303 and "Switch+back" in r.headers["location"], path
        upd.assert_not_awaited()


# ── /coach routes ────────────────────────────────────────────────────────────

def test_switch_refuses_an_athlete_who_is_not_linked():
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=False)):
        r = _client({"athlete_id": 1}, follow=False).post("/coach/switch", data={"athlete_id": "9"})
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_switch_then_back_to_me():
    c = _client({"athlete_id": 1, "athlete_name": "Paul"}, follow=False)
    athletes = [{"id": 7, "name": "Sam"}]
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
         patch("web.queries.coach.list_coached_athletes", new=AsyncMock(return_value=athletes)):
        r = c.post("/coach/switch", data={"athlete_id": "7"})
        assert r.status_code == 303 and r.headers["location"] == "/"
        with patch("web.queries.profile.get_athlete", new=AsyncMock(return_value=_full_athlete())) as ga, \
             patch("web.queries.profile.get_active_goal", new=AsyncMock(return_value=None)):
            c.get("/profile")
            assert ga.await_args.args[1] == 7
            c.post("/coach/switch", data={"athlete_id": "0"})
            c.get("/profile")
            assert ga.await_args.args[1] == 1


def test_grant_checks_the_username():
    c = _client({"athlete_id": 1}, follow=False)
    with patch("web.queries.coach.find_account_by_username", new=AsyncMock(return_value=None)):
        assert "error=No+account" in c.post("/coach/grant", data={"coach_username": "nobody"}).headers["location"]
    with patch("web.queries.coach.find_account_by_username",
               new=AsyncMock(return_value={"id": 1, "name": "Me", "username": "me"})):
        assert "own+account" in c.post("/coach/grant", data={"coach_username": "me"}).headers["location"]
    with patch("web.queries.coach.find_account_by_username",
               new=AsyncMock(return_value={"id": 3, "name": "Paul", "username": "paul"})), \
         patch("web.queries.coach.grant", new=AsyncMock(return_value=True)) as grant:
        assert "can+now+coach" in c.post("/coach/grant", data={"coach_username": "paul"}).headers["location"]
    grant.assert_awaited_once()
    assert grant.await_args.args[1:] == (1, 3)                               # (athlete = me, coach = Paul)


def test_coach_routes_act_as_the_account_not_the_coached_athlete():
    """Granting while coaching Sam must grant on the coach's own account."""
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
         patch("web.queries.coach.find_account_by_username",
               new=AsyncMock(return_value={"id": 3, "name": "Kim", "username": "kim"})), \
         patch("web.queries.coach.grant", new=AsyncMock(return_value=True)) as grant:
        _client(ACTING, follow=False).post("/coach/grant", data={"coach_username": "kim"})
    assert grant.await_args.args[1] == 1


def test_add_athlete_validates_and_creates():
    c = _client({"athlete_id": 1}, follow=False)
    with patch("web.queries.coach.create_managed_athlete", new=AsyncMock(return_value=12)) as create:
        assert "error=" in c.post("/coach/athletes", data={"name": "Sam", "level": "pro"}).headers["location"]
        r = c.post("/coach/athletes", data={"name": " Sam ", "level": "beginner", "sessions_per_week": "99",
                                            "bodyweight_kg": "500", "biological_sex": "x"})
    assert "msg=Added+Sam" in r.headers["location"]
    kw = create.await_args.kwargs
    assert kw == {"name": "Sam", "level": "beginner", "sessions_per_week": 4, "bodyweight_kg": None,
                  "biological_sex": None}


def test_removing_the_coached_athlete_stops_acting():
    c = _client(ACTING, follow=False)
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
         patch("web.queries.coach.remove_athlete", new=AsyncMock(return_value="deleted")):
        r = c.post("/coach/remove", data={"athlete_id": "7"})
    assert "msg=Athlete+deleted" in r.headers["location"]
    with patch("web.queries.coach.is_linked", new=AsyncMock(return_value=True)), \
         patch("web.queries.profile.get_athlete", new=AsyncMock(return_value=_full_athlete())) as ga, \
         patch("web.queries.profile.get_active_goal", new=AsyncMock(return_value=None)):
        c.get("/profile")
    assert ga.await_args.args[1] == 1


def test_coach_page_lists_both_sides():
    athletes = [{"id": 7, "name": "Sam O'Neil", "level": "beginner", "sessions_per_week": 3, "origin": "created",
                 "has_login": False, "active_program": None}]
    coaches = [{"id": 3, "name": "Kim", "username": "kim", "origin": "granted", "created_at": None}]
    with patch("web.queries.coach.list_coached_athletes", new=AsyncMock(return_value=athletes)), \
         patch("web.queries.coach.list_coaches", new=AsyncMock(return_value=coaches)):
        html = _client({"athlete_id": 1}).get("/coach").text
    assert "Coach Sam" in html and "no login (added by you)" in html and "Remove access" in html
    assert "confirm(this.dataset.confirm)" in html and "O&#39;Neil" in html     # the name never reaches inline JS raw


# ── Queries ──────────────────────────────────────────────────────────────────

def test_remove_deletes_only_an_unreachable_created_athlete():
    from web.queries import coach as q

    def run(row):
        with patch("web.queries.coach.async_fetch_one", new=AsyncMock(return_value=row)), \
             patch("web.queries.coach.async_execute", new=AsyncMock()) as ex:
            out = asyncio.run(q.remove_athlete(object(), 1, 7))
        return out, [c.args[1].split()[0:3] for c in ex.await_args_list]

    assert run(None) == (None, [])
    assert run({"origin": "created", "has_login": False, "other_coaches": 0})[0] == "deleted"
    assert run({"origin": "created", "has_login": False, "other_coaches": 0})[1] == [["DELETE", "FROM", "athletes"]]
    for row in ({"origin": "granted", "has_login": True, "other_coaches": 0},
                {"origin": "created", "has_login": True, "other_coaches": 0},          # they set up a login since
                {"origin": "created", "has_login": False, "other_coaches": 1}):        # another coach still has them
        out, calls = run(row)
        assert out == "unlinked" and calls == [["DELETE", "FROM", "coach_athletes"]], row


# ── Nav ──────────────────────────────────────────────────────────────────────

def test_banner_only_while_coaching():
    assert "Back to me" not in _render_base({"athlete_id": 1, "athlete_name": "Paul"})
    html = _render_base(ACTING)
    assert "Coaching <span" in html and "Sam" in html and "Back to me" in html and 'href="/coach"' in html
