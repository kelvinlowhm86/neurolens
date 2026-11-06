"""M3k's page copy and operator script (docs/M3k_spec.md §8-§9, §11).

The free minutes come from `billing.invite_cents` and the view minimum from
`research.curve_min_views`; neither number is typed into a page. The "1,000+ followers"
screening bar is plain copy, not tied to that setting. The request panel tells a user which of
the four states they are in, and the operator can list waiting requests and decline one only
after confirming.
"""

import importlib.util
import re

import pytest
from conftest import ISSUER, REPO_ROOT
from neurolens import billing, pricing, settings

STARTER_CENTS = 10_000
CHANNEL = "https://www.youtube.com/@anncreates"
OTHER_CHANNEL = "https://www.facebook.com/anncreates"

SCRIPT = REPO_ROOT / "infra" / "access_requests.py"


def page_text(name):
    html = (REPO_ROOT / "static" / name).read_text()
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def config_numbers():
    cfg = settings.load_settings()
    minutes = (
        cfg["billing"]["invite_cents"] * pricing.BLOCK_SECONDS / (pricing.CENTS_PER_BLOCK * 60)
    )
    return minutes, cfg["research"]["curve_min_views"]


# ---------------------------------------------------------------- landing page


def test_the_landing_page_offers_the_configured_free_minutes():
    minutes, _ = config_numbers()
    assert minutes == 5

    assert f"Creators and marketers get {minutes:g} free minutes" in page_text("index.html")


def test_the_landing_page_names_the_three_ways_in():
    text = page_text("index.html")
    html = (REPO_ROOT / "static" / "index.html").read_text()

    for phrase in (
        "Samples",
        "Open",
        "Explore every sample without an account.",
        "Your videos",
        "Free to try",
        "Shared",
        "Keep going",
        "Add a video's retention curve and earn its credit back.",
        "Get free minutes",
        "For channels with 1,000+ followers.",
    ):
        assert phrase in text
    assert re.search(r'<a[^>]*href="/login"[^>]*>.*?Get free minutes.*?</a>', html, re.S)


# ---------------------------------------------------------------- the configured minimum


@pytest.mark.parametrize("name", ["retention_guide.html", "privacy.html", "app.html"])
def test_each_page_shows_the_configured_view_minimum(name):
    _, min_views = config_numbers()
    assert min_views == 1000

    assert f"{min_views:,}" in page_text(name)


@pytest.mark.parametrize("name", ["index.html", "retention_guide.html", "privacy.html", "app.html"])
def test_no_page_still_calls_access_invited(name):
    assert "invited" not in page_text(name).lower()


def test_the_guide_gives_the_views_line_on_youtube_and_facebook():
    _, min_views = config_numbers()
    html = (REPO_ROOT / "static" / "retention_guide.html").read_text()
    youtube = html.split('id="youtube"', 1)[1].split("<h2", 1)[0]
    facebook = html.split('id="facebook"', 1)[1].split("<h2", 1)[0]

    def text(fragment):
        return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())

    line = f"Add it once the video has {min_views:,} views to get its credit back."

    assert line in text(youtube)
    assert line in text(facebook)
    assert "week" not in text(youtube) + text(facebook)


def test_the_privacy_page_names_the_access_request_data():
    text = page_text("privacy.html")

    assert "An access request, if you ask for free minutes" in text
    assert "your channel link and any note" in text
    assert "It is emailed to the NeuroLens team (through Amazon Web Services)" in text
    assert "kept until you delete your account" in text


def test_the_privacy_page_states_the_view_rule():
    _, min_views = config_numbers()

    assert f"and, when we can check it, has at least {min_views:,} views" in page_text(
        "privacy.html"
    )


# ---------------------------------------------------------------- the request panel


def test_the_panel_asks_for_a_channel_and_a_note():
    text = page_text("app.html")

    assert (
        "For creators and marketers with 1,000+ followers. We look at each channel by hand."
    ) in text
    for phrase in (
        "Channel link",
        "https://www.youtube.com/@yourchannel",
        "Anything else? (optional)",
        "Ask for minutes",
    ):
        assert phrase in text


def test_the_panel_tells_a_requester_they_are_waiting():
    assert "Request received. We'll look at your channel and add your minutes here." in page_text(
        "app.html"
    )


def test_the_panel_explains_a_decline():
    assert (
        "Thanks for asking. Free minutes are for channels with 1,000+ followers for now."
        in page_text("app.html")
    )


def test_the_panel_explains_a_funded_account_with_no_credit():
    assert (
        "You've used your credit. Add a shared video's retention curve to get its credit back, "
        "or ask the NeuroLens team for more."
    ) in page_text("app.html")


# ---------------------------------------------------------------- /api/limits


def test_limits_publish_the_invite_seconds_from_the_configured_credit(aws, make_cfg, tmp_path):
    from neurolens.web.app import create_app

    invite_cents = 1000
    cfg = make_cfg(billing={"starter_cents": 0, "invite_cents": invite_cents})
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", s3_client=aws.s3)
    app.config["TESTING"] = True

    data = app.test_client().get("/api/limits").get_json()

    assert data["invite_seconds"] == invite_cents * pricing.BLOCK_SECONDS / pricing.CENTS_PER_BLOCK


# ---------------------------------------------------------------- the operator script


def load_script():
    spec = importlib.util.spec_from_file_location("access_requests_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def answers(*replies):
    replies = iter(replies)
    return lambda prompt: next(replies)


def test_the_script_lists_waiting_requests_oldest_first(db, pg, capsys):
    from neurolens import access

    module = load_script()
    first = billing.sign_in(db, ISSUER, "sub-old", "old@example.com", 0)
    second = billing.sign_in(db, ISSUER, "sub-new", "new@example.com", 0)
    granted = billing.sign_in(db, ISSUER, "sub-done", "done@example.com", 0)
    access.add_request(db, first, CHANNEL, "old request")
    access.add_request(db, second, OTHER_CHANNEL, "new request")
    access.add_request(db, granted, CHANNEL, None)
    billing.grant_credit(db, "done@example.com", 100)
    pg.conn.execute(
        "UPDATE access_requests SET created_at = now() - interval '1 hour' WHERE user_id = %s",
        (first,),
    )

    assert module.main(["list", "--backend", "postgres"], db=db) == 0

    out = capsys.readouterr().out
    assert "old@example.com" in out
    assert "new@example.com" in out
    assert "done@example.com" not in out
    assert CHANNEL in out
    assert "old request" in out
    assert out.index("old@example.com") < out.index("new@example.com")


def test_the_script_declines_nothing_without_a_yes(db):
    from neurolens import access

    module = load_script()
    user_id = billing.sign_in(db, ISSUER, "sub-decline", "decline@example.com", 0)
    access.add_request(db, user_id, CHANNEL, None)

    module.main(
        ["decline", "--backend", "postgres"],
        ask=answers("decline@example.com", "n"),
        db=db,
    )

    assert access.request_status(db, user_id) == "waiting"
