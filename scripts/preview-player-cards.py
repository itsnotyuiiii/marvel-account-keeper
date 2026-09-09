"""Actual Add/Save UI with synthetic in-memory API replies; no vault or Internet.

Use only the fixture identities shown on the page. This module never imports
the production backend or reads game/session/account files.
"""
import time
from pathlib import Path
from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
CARDS = []
CALLS = []


@app.after_request
def local_only(response):
    response.headers["Content-Security-Policy"] = ("default-src 'self'; connect-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; object-src 'none'")
    return response


@app.get("/")
def index():
    return render_template("index.html").replace("</body>",
        '<aside style="position:fixed;bottom:8px;left:8px;z-index:20000;background:#172019;color:#e0f6da;padding:8px;font:12px monospace;pointer-events:none">'
        'Synthetic fixture only · UID 123456789 · IGN FixtureIGN · restricted IGN PrivateCareer</aside></body>')


@app.get("/api/status")
def status():
    return jsonify(initialized=True, unlocked=True, stats_provider="netease", account_count=len(CARDS),
                   lockout_minutes=0, lock_in_s=0, ui_options={}, version="fixture", commit="synthetic-only")


@app.route("/api/accounts", methods=["GET", "POST"])
def accounts():
    if request.method == "GET":
        return jsonify(accounts=CARDS)
    body = request.get_json()
    CALLS.append({"method": "POST", "path": "/api/accounts", "auto_lookup": body.get("refresh_stats") is True})
    if body.get("refresh_stats") is not True:
        return jsonify(error="Fixture requires the one-action lookup flag"), 400
    card = dict(body, id=str(len(CARDS)+1), stats_provider="netease", netease_connected=True)
    lookup(card)
    CARDS.append(card)
    return jsonify(ok=True, id=card["id"], account=card)


def lookup(card):
    uid, name = card.get("rivals_uid"), card.get("in_game_name")
    private = name == "PrivateCareer" or uid == "222333444"
    if uid == "123456789":
        name = "Synthetic UID player"
    elif name == "FixtureIGN" or uid == "987654321":
        uid, name = "987654321", "FixtureIGN"
    elif private:
        uid, name = "222333444", "PrivateCareer"
    else:
        card.update(last_refresh_status="not_found", last_refresh_code="not_found",
                    last_refresh_error="Use one of the synthetic fixture identities.")
        return
    card.update(rivals_uid=uid, in_game_name=name, rivals_platform="pc", last_refresh_source="netease",
                last_refresh_ts=int(time.time()), netease_profile=None,
                netease_visibility="restricted" if private else "public")
    if private:
        card.update(last_refresh_status="error", last_refresh_code="career_profile_restricted",
                    last_refresh_error="This Career overview is restricted. Saved ranks were kept.",
                    netease_profile_note="Career data is not public for this platform.")
    else:
        card.update(current_rank="Diamond II", current_points=4350, peak_rank="Celestial I", peak_points=5016,
                    last_refresh_status="ok", last_refresh_code=None, last_refresh_error=None,
                    netease_rank={"season":19, "qualification":"ranked", "placements_completed":10,
                                  "record":{"matches":27,"wins":21}},
                    netease_profile_note="Identity and rank lookup are connected; detailed Career stats for this player are not connected yet.")


@app.put("/api/accounts/<card_id>")
def edit(card_id):
    card = next(c for c in CARDS if c["id"] == card_id)
    card.update(request.get_json())
    CALLS.append({"method":"PUT", "path":request.path})
    return jsonify(ok=True)


@app.post("/api/accounts/<card_id>/refresh-stats")
def refresh(card_id):
    card = next(c for c in CARDS if c["id"] == card_id)
    lookup(card)
    CALLS.append({"method":"POST", "path":request.path})
    return jsonify(ok=True, account=card)


@app.get("/api/career/status")
def career_status():
    return jsonify(configured=True, uid="555666777", platform="pc", season=19, seasons=[19],
                   expires_at=int(time.time())+3600, rank_available=True, rank_season=19,
                   player_lookup_available=True)


@app.get("/fixture/calls")
def fixture_calls():
    return jsonify(calls=CALLS, synthetic_only=True)


@app.route("/api/<path:unused>", methods=["GET", "POST"])
def ancillary(unused):
    return jsonify(ok=True, active=None, uids=[], has_update=False)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=55032, debug=False)
