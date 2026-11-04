"""Early access by request (M3k §4): one channel link per account, screened by hand.

The request row, not the notification email, is the list of requests. Every function takes a
`Database` first and does its work in one transaction.
"""

from urllib.parse import urlsplit

MESSAGE = "Paste your YouTube, Facebook, TikTok or Instagram channel link."
MAX_URL_CHARS = 300
MAX_NOTE_CHARS = 500
PLATFORMS = {
    "youtube.com": "youtube",
    "facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "instagram.com": "instagram",
}


class AlreadyRequested(Exception):
    """This account already asked for free minutes (M3k §4)."""


def platform_of(url):
    """The platform named by a channel link (M3k §4).

    Only `http`/`https` links to one of the four platforms, with a path other than empty or `/`,
    are accepted. The host is lowercased and one leading `www.` or `m.` is ignored. Anything else
    raises ValueError with the one message the page shows.
    """
    if not isinstance(url, str) or len(url) > MAX_URL_CHARS:
        raise ValueError(MESSAGE)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(MESSAGE)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    elif host.startswith("m."):
        host = host[2:]
    platform = PLATFORMS.get(host)
    if platform is None or parts.path in ("", "/"):
        raise ValueError(MESSAGE)
    return platform


def _clean_note(note):
    """The note stripped, with empty text stored as null; at most 500 characters (M3k §4)."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValueError("The note must be text.")
    note = note.strip()
    if not note:
        return None
    if len(note) > MAX_NOTE_CHARS:
        raise ValueError(f"Keep the note to {MAX_NOTE_CHARS} characters or fewer.")
    return note


def add_request(db, user_id, channel_url, note):
    """Store a waiting request, once per account, and return it (M3k §4).

    Validates the link and note first, so a bad request writes nothing. An account that already
    has a request raises AlreadyRequested and keeps its first one.
    """
    platform = platform_of(channel_url)
    note = _clean_note(note)
    with db.transaction() as tx:
        rows = tx.execute(
            "INSERT INTO access_requests (user_id, channel_url, platform, note) "
            "VALUES (:user_id, :channel_url, :platform, :note) "
            "ON CONFLICT DO NOTHING RETURNING *",
            {
                "user_id": user_id,
                "channel_url": channel_url,
                "platform": platform,
                "note": note,
            },
        )
    if not rows:
        raise AlreadyRequested("this account already asked for free minutes")
    return rows[0]


def request_status(db, user_id):
    """The account's request status, or None when it has not asked (M3k §4)."""
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT status FROM access_requests WHERE user_id = :user_id", {"user_id": user_id}
        )
    return rows[0]["status"] if rows else None


def waiting_requests(db):
    """Every waiting request with its account's email, oldest first (M3k §4, §9)."""
    with db.transaction() as tx:
        return tx.execute(
            "SELECT r.user_id, u.email, r.channel_url, r.platform, r.note, r.created_at "
            "FROM access_requests r JOIN users u ON u.user_id = r.user_id "
            "WHERE r.status = :waiting ORDER BY r.created_at",
            {"waiting": "waiting"},
        )


def decline(db, email):
    """Decline the account's waiting request (M3k §4, §9). False when there is none."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE access_requests SET status = :declined, decided_at = now() "
            "WHERE status = :waiting AND user_id IN "
            "(SELECT user_id FROM users WHERE lower(email) = lower(:email)) RETURNING user_id",
            {"declined": "declined", "waiting": "waiting", "email": email},
        )
    return bool(rows)
