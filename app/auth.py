from typing import Any

from .db import Database
from .models import utcnow

GLOBAL_ADMINISTRATION = "Global Administration"


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    def login_google_user(self, profile: dict[str, Any]) -> dict:
        google_sub = str(profile.get("sub", "")).strip()
        email = str(profile.get("email", "")).strip()
        if not google_sub or not email:
            raise ValueError("Google hat keine eindeutige Benutzer-ID oder E-Mail geliefert")
        if profile.get("email_verified") is False:
            raise ValueError("Das Google-Konto hat keine bestätigte E-Mail-Adresse")
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute("SELECT * FROM users WHERE google_sub=?", (google_sub,)).fetchone()
            if user:
                conn.execute(
                    "UPDATE users SET email=?, name=?, picture_url=?, last_login_at=? WHERE id=?",
                    (email, profile.get("name", ""), profile.get("picture"), now, user["id"]),
                )
                user_id = user["id"]
            else:
                is_first_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                cursor = conn.execute(
                    "INSERT INTO users(google_sub,email,name,picture_url,created_at,last_login_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (google_sub, email, profile.get("name", ""), profile.get("picture"), now, now),
                )
                user_id = cursor.lastrowid
                if is_first_user:
                    conn.execute(
                        "INSERT INTO user_permissions(user_id,permission,granted_at) VALUES(?,?,?)",
                        (user_id, GLOBAL_ADMINISTRATION, now),
                    )
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return None
            user = dict(row)
            user["permissions"] = [r["permission"] for r in conn.execute(
                "SELECT permission FROM user_permissions WHERE user_id=? ORDER BY permission", (user_id,)
            )]
            return user

    def list_users(self) -> list[dict]:
        with self.db.connect() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM users ORDER BY created_at")]
        return [user for user_id in ids if (user := self.get_user(user_id))]

    def set_permission(self, user_id: int, permission: str, enabled: bool) -> None:
        with self.db.connect() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise LookupError("Benutzer nicht gefunden")
            if enabled:
                conn.execute(
                    "INSERT OR IGNORE INTO user_permissions(user_id,permission,granted_at) VALUES(?,?,?)",
                    (user_id, permission, utcnow()),
                )
            else:
                if permission == GLOBAL_ADMINISTRATION:
                    admins = conn.execute(
                        "SELECT COUNT(*) FROM user_permissions WHERE permission=?", (permission,)
                    ).fetchone()[0]
                    has_permission = conn.execute(
                        "SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?",
                        (user_id, permission),
                    ).fetchone()
                    if has_permission and admins <= 1:
                        raise ValueError("Die letzte globale Administration kann nicht entfernt werden")
                conn.execute(
                    "DELETE FROM user_permissions WHERE user_id=? AND permission=?", (user_id, permission)
                )
