import base64
import binascii
import ipaddress
from io import BytesIO
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from flask import Flask, jsonify, make_response, redirect, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, "database", ".env")
DATABASE_PATH = os.environ.get(
    "NETWERK_DATABASE_PATH",
    os.path.join(BASE_DIR, "database", "sql", "netwerk.db")
)
JSON_DATA_DIR = os.environ.get(
    "NETWERK_JSON_DIR",
    os.path.join(BASE_DIR, "database", "json")
)
BAN_LIST_PATH = os.path.join(JSON_DATA_DIR, "banned.json")
JSON_SYNC_LOCK = threading.Lock()


def load_mail_environment():
    if not os.path.isfile(ENV_PATH):
        return

    with open(ENV_PATH, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key in {"SERVICE", "APP_PASSWORD"}:
                os.environ.setdefault(key, value)


load_mail_environment()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(days=30)


@contextmanager
def connect_database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    database_directory = os.path.dirname(DATABASE_PATH)
    if database_directory:
        os.makedirs(database_directory, exist_ok=True)

    with connect_database() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                verification_code_hash TEXT,
                verification_expires_at INTEGER,
                profile_photo BLOB,
                profile_photo_mime TEXT,
                profile_photo_version INTEGER NOT NULL DEFAULT 0
            )
        """)
        user_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)")
        }
        if "profile_photo" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN profile_photo BLOB")
        if "profile_photo_mime" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN profile_photo_mime TEXT")
        if "profile_photo_version" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN profile_photo_version INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS login_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                ip_address TEXT,
                logged_in_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS direct_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (sender_id) REFERENCES users (id),
                FOREIGN KEY (recipient_id) REFERENCES users (id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS chat_presence (
                user_id INTEGER PRIMARY KEY,
                last_seen_at INTEGER NOT NULL,
                typing_to_id INTEGER,
                typing_at INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)


def load_ban_list():
    if not os.path.exists(BAN_LIST_PATH):
        return {"usernames": [], "ip_addresses": []}

    with open(BAN_LIST_PATH, encoding="utf-8") as ban_file:
        ban_list = json.load(ban_file)

    if (
        not isinstance(ban_list, dict)
        or not isinstance(ban_list.get("usernames"), list)
        or not isinstance(ban_list.get("ip_addresses"), list)
    ):
        raise ValueError(
            "banned.json must contain 'usernames' and 'ip_addresses' arrays."
        )

    return ban_list


def find_ban(username=None, ip_address=None):
    ban_list = load_ban_list()

    if username:
        normalized_username = username.strip().casefold()
        for entry in ban_list["usernames"]:
            if not isinstance(entry, dict):
                raise ValueError("Each username ban must be a JSON object.")
            banned_username = entry.get("username")
            if not isinstance(banned_username, str):
                raise ValueError("Each username ban needs a username string.")
            if banned_username.strip().casefold() == normalized_username:
                reason = entry.get("reason") or "This account has been banned."
                if not isinstance(reason, str):
                    raise ValueError("A username ban reason must be a string.")
                return {
                    "type": "account",
                    "reason": reason
                }

    if ip_address:
        for entry in ban_list["ip_addresses"]:
            if not isinstance(entry, dict):
                raise ValueError("Each IP ban must be a JSON object.")
            banned_ip = entry.get("ip_address")
            if not isinstance(banned_ip, str):
                raise ValueError("Each IP ban needs an ip_address string.")
            if banned_ip.strip() == ip_address:
                reason = entry.get("reason") or "This IP address has been banned."
                if not isinstance(reason, str):
                    raise ValueError("An IP ban reason must be a string.")
                return {
                    "type": "ip",
                    "reason": reason
                }

    return None


def get_admin_user(user_id):
    if user_id is None:
        return None

    with connect_database() as connection:
        user = connection.execute(
            "SELECT id, username FROM users WHERE id = ? AND verified = 1",
            (user_id,)
        ).fetchone()

    if user is None or user["username"].casefold() != "koutroufinis":
        return None
    return user


def require_admin():
    user = get_admin_user(session.get("user_id"))
    if user is None:
        return None, (jsonify({
            "success": False,
            "message": "You do not have permission to use the control panel."
        }), 403)
    return user, None


def current_request_ban():
    ip_ban = find_ban(ip_address=request.remote_addr)
    if ip_ban:
        return ip_ban

    user_id = session.get("user_id") or session.get("pending_user_id")
    if user_id is None:
        return None

    with connect_database() as connection:
        user = connection.execute(
            "SELECT username FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

    return find_ban(username=user["username"]) if user else None


def json_ban_response(ban):
    return jsonify({
        "success": False,
        "banned": True,
        "ban_type": ban["type"],
        "reason": ban["reason"],
        "message": "Access to Netwerk is restricted."
    }), 403


def write_json_snapshot(path, data):
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=JSON_DATA_DIR,
            prefix=".netwerk-",
            suffix=".tmp",
            delete=False
        ) as json_file:
            temporary_path = json_file.name
            json.dump(data, json_file, ensure_ascii=False, indent=2)
            json_file.write("\n")
            json_file.flush()
            os.fsync(json_file.fileno())

        os.replace(temporary_path, path)
    except OSError:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise


def sync_json_data():
    os.makedirs(JSON_DATA_DIR, exist_ok=True)

    with JSON_SYNC_LOCK:
        with connect_database() as connection:
            users = [
                {
                    "id": row["id"],
                    "username": row["username"],
                    "email": row["email"],
                    "verified": bool(row["verified"])
                }
                for row in connection.execute(
                    "SELECT id, username, email, verified FROM users ORDER BY id"
                )
            ]
            login_history = [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "email": row["email"],
                    "ip_address": row["ip_address"],
                    "logged_in_at": row["logged_in_at"]
                }
                for row in connection.execute(
                    """
                    SELECT id, user_id, username, email, ip_address, logged_in_at
                    FROM login_history
                    ORDER BY id
                    """
                )
            ]
            messages = [
                {
                    "id": row["id"],
                    "sender_id": row["sender_id"],
                    "recipient_id": row["recipient_id"],
                    "body": row["body"],
                    "created_at": row["created_at"]
                }
                for row in connection.execute(
                    """
                    SELECT id, sender_id, recipient_id, body, created_at
                    FROM direct_messages
                    ORDER BY id
                    """
                )
            ]

        write_json_snapshot(
            os.path.join(JSON_DATA_DIR, "users.json"),
            users
        )
        write_json_snapshot(
            os.path.join(JSON_DATA_DIR, "login_history.json"),
            login_history
        )
        write_json_snapshot(
            os.path.join(JSON_DATA_DIR, "messages.json"),
            messages
        )


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def save_verification_code(connection, user_id, code):
    connection.execute(
        """
        UPDATE users
        SET verification_code_hash = ?, verification_expires_at = ?
        WHERE id = ?
        """,
        (
            generate_password_hash(code),
            int(time.time()) + 600,
            user_id
        )
    )


def send_verification_email(email, code):
    sender = os.environ.get("SERVICE", "").strip()
    password = os.environ.get("APP_PASSWORD", "").replace(" ", "")
    if not sender or not password:
        raise ValueError("Email sender credentials are not configured.")

    message = EmailMessage()
    message["Subject"] = "Your Netwerk verification code"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "Use this 6-digit code to verify your Netwerk account:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not create this account, "
        "you can ignore this email."
    )

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(sender, password)
        smtp.send_message(message)


def record_login(connection, user, ip_address):
    connection.execute(
        """
        INSERT INTO login_history (
            user_id, username, email, ip_address, logged_in_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            user["username"],
            user["email"],
            ip_address,
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    )


initialize_database()
sync_json_data()
os.makedirs(JSON_DATA_DIR, exist_ok=True)
if not os.path.exists(BAN_LIST_PATH):
    with open(BAN_LIST_PATH, "w", encoding="utf-8") as ban_file:
        json.dump({"usernames": [], "ip_addresses": []}, ban_file, indent=2)
        ban_file.write("\n")


@app.before_request
def enforce_bans():
    if request.endpoint in {"banned", "ban_status", "logout", "delete_account"}:
        return None

    if request.path in {
        "/resources/static/css/banned.css",
        "/resources/static/js/banned.js"
    }:
        return None

    ban = current_request_ban()
    if ban is None:
        return None

    if request.path in {
        "/session",
        "/login",
        "/signup",
        "/verify",
        "/resend-verification"
    } or request.path.startswith(("/chat/", "/account/")):
        return json_ban_response(ban)

    return redirect("/banned")



@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/verification")
def verification():
    return send_from_directory(
        os.path.join(BASE_DIR, "resources", "static", "sites"),
        "verification.html"
    )


@app.route("/interface")
def chat_interface():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect("/")

    with connect_database() as connection:
        user = connection.execute(
            "SELECT id FROM users WHERE id = ? AND verified = 1",
            (user_id,)
        ).fetchone()

    if user is None:
        session.clear()
        return redirect("/")

    return send_from_directory(
        os.path.join(BASE_DIR, "resources", "static", "sites"),
        "interface.html"
    )


def get_chat_user(user_id):
    with connect_database() as connection:
        return connection.execute(
            """
            SELECT id, username, profile_photo_version
            FROM users
            WHERE id = ? AND verified = 1
            """,
            (user_id,)
        ).fetchone()


def find_chat_recipient(recipient_id):
    user = get_chat_user(recipient_id)
    if user is None:
        return None
    if find_ban(username=user["username"]):
        return None
    return user


def serialize_message(row, current_user_id):
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "recipient_id": row["recipient_id"],
        "text": row["body"],
        "created_at": row["created_at"],
        "sent": row["sender_id"] == current_user_id
    }


@app.route("/account/profile", methods=["POST"])
def update_profile():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Sign in to update your profile."
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    username = data.get("username")
    if not isinstance(username, str) or not username.strip():
        return jsonify({
            "success": False,
            "message": "Enter a username."
        }), 400
    username = username.strip()
    if len(username) > 30:
        return jsonify({
            "success": False,
            "message": "Usernames must be 30 characters or fewer."
        }), 400
    admin_user = get_admin_user(user_id)
    if admin_user is not None and username.casefold() != admin_user["username"].casefold():
        return jsonify({
            "success": False,
            "message": "The control-panel administrator username cannot be changed."
        }), 400

    remove_photo = data.get("remove_photo", False)
    if not isinstance(remove_photo, bool):
        return jsonify({
            "success": False,
            "message": "The remove-photo value must be true or false."
        }), 400

    photo = None
    photo_mime = None
    photo_data = data.get("photo")
    if photo_data is not None:
        if remove_photo or not isinstance(photo_data, str):
            return jsonify({
                "success": False,
                "message": "Choose a valid profile photo."
            }), 400
        if len(photo_data) > 700_000:
            return jsonify({
                "success": False,
                "message": "Profile photos must be 512 KB or smaller."
            }), 413
        try:
            photo = base64.b64decode(photo_data, validate=True)
        except (ValueError, binascii.Error):
            return jsonify({
                "success": False,
                "message": "Choose a valid profile photo."
            }), 400
        if len(photo) > 512_000:
            return jsonify({
                "success": False,
                "message": "Profile photos must be 512 KB or smaller."
            }), 413
        if photo.startswith(b"\x89PNG\r\n\x1a\n"):
            photo_mime = "image/png"
        elif photo.startswith(b"\xff\xd8\xff"):
            photo_mime = "image/jpeg"
        elif (
            len(photo) >= 12
            and photo.startswith(b"RIFF")
            and photo[8:12] == b"WEBP"
        ):
            photo_mime = "image/webp"
        else:
            return jsonify({
                "success": False,
                "message": "Use a PNG, JPEG, or WebP profile photo."
            }), 400

    if find_ban(username=username):
        return jsonify({
            "success": False,
            "message": "That username is unavailable."
        }), 400

    try:
        with connect_database() as connection:
            if remove_photo:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, profile_photo = NULL,
                        profile_photo_mime = NULL, profile_photo_version = ?
                    WHERE id = ? AND verified = 1
                    """,
                    (username, int(time.time()), user_id)
                )
            elif photo is not None:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, profile_photo = ?,
                        profile_photo_mime = ?, profile_photo_version = ?
                    WHERE id = ? AND verified = 1
                    """,
                    (username, photo, photo_mime, int(time.time()), user_id)
                )
            else:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?
                    WHERE id = ? AND verified = 1
                    """,
                    (username, user_id)
                )
            user = connection.execute(
                """
                SELECT id, username, profile_photo_version,
                       profile_photo IS NOT NULL AS has_profile_photo
                FROM users
                WHERE id = ? AND verified = 1
                """,
                (user_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({
            "success": False,
            "message": "That username is already in use."
        }), 409

    if user is None:
        session.clear()
        return jsonify({
            "success": False,
            "message": "Your account is no longer available."
        }), 401

    sync_json_data()
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "profile_photo_version": user["profile_photo_version"],
            "has_profile_photo": bool(user["has_profile_photo"])
        }
    })


@app.route("/account/delete", methods=["POST"])
def delete_account():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Sign in to delete your account."
        }), 401

    data = request.get_json(silent=True)
    password = data.get("password") if isinstance(data, dict) else None
    if not isinstance(password, str) or not password:
        return jsonify({
            "success": False,
            "message": "Enter your password to delete your account."
        }), 400

    with connect_database() as connection:
        user = connection.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE id = ? AND verified = 1
            """,
            (user_id,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            return jsonify({
                "success": False,
                "message": "The password is incorrect."
            }), 403

        connection.execute(
            "DELETE FROM direct_messages WHERE sender_id = ? OR recipient_id = ?",
            (user_id, user_id)
        )
        connection.execute(
            "DELETE FROM login_history WHERE user_id = ?",
            (user_id,)
        )
        connection.execute("DELETE FROM chat_presence WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    session.clear()
    sync_json_data()
    return jsonify({"success": True})


@app.route("/chat/profile-photo/<int:user_id>")
def profile_photo(user_id):
    if session.get("user_id") is None or get_chat_user(session["user_id"]) is None:
        session.clear()
        return jsonify({
            "success": False,
            "message": "Sign in to view profile photos."
        }), 401

    with connect_database() as connection:
        user = connection.execute(
            """
            SELECT profile_photo, profile_photo_mime
            FROM users
            WHERE id = ? AND verified = 1
            """,
            (user_id,)
        ).fetchone()

    if user is None or user["profile_photo"] is None:
        return "", 404

    response = make_response(send_file(
        BytesIO(user["profile_photo"]),
        mimetype=user["profile_photo_mime"],
        download_name="profile-photo",
        conditional=False
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/chat/users")
def chat_users():
    user_id = session.get("user_id")
    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Sign in to use chat."
        }), 401

    current_user = get_chat_user(user_id)
    if current_user is None:
        session.clear()
        return jsonify({
            "success": False,
            "message": "Sign in to use chat."
        }), 401

    with connect_database() as connection:
        rows = connection.execute(
            """
            SELECT
                users.id,
                users.username,
                users.profile_photo_version,
                users.profile_photo IS NOT NULL AS has_profile_photo,
                COALESCE(chat_presence.last_seen_at, 0) AS last_seen_at,
                (
                    SELECT direct_messages.body
                    FROM direct_messages
                    WHERE
                        (direct_messages.sender_id = ? AND direct_messages.recipient_id = users.id)
                        OR (direct_messages.sender_id = users.id AND direct_messages.recipient_id = ?)
                    ORDER BY direct_messages.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT direct_messages.created_at
                    FROM direct_messages
                    WHERE
                        (direct_messages.sender_id = ? AND direct_messages.recipient_id = users.id)
                        OR (direct_messages.sender_id = users.id AND direct_messages.recipient_id = ?)
                    ORDER BY direct_messages.id DESC
                    LIMIT 1
                ) AS last_message_at
            FROM users
            LEFT JOIN chat_presence ON chat_presence.user_id = users.id
            WHERE users.id != ? AND users.verified = 1
            ORDER BY COALESCE(last_message_at, '') DESC, users.username COLLATE NOCASE
            """,
            (user_id, user_id, user_id, user_id, user_id)
        ).fetchall()

    contacts = []
    for row in rows:
        if find_ban(username=row["username"]):
            continue
        contacts.append({
            "id": row["id"],
            "username": row["username"],
            "profile_photo_version": row["profile_photo_version"],
            "has_profile_photo": bool(row["has_profile_photo"]),
            "online": row["last_seen_at"] >= int(time.time()) - 30,
            "last_message": row["last_message"] or "",
            "last_message_at": row["last_message_at"]
        })

    return jsonify({
        "success": True,
        "users": contacts
    })


@app.route("/chat/presence", methods=["POST"])
def chat_presence():
    user_id = session.get("user_id")
    if user_id is None or get_chat_user(user_id) is None:
        session.clear()
        return jsonify({
            "success": False,
            "message": "Sign in to use chat."
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    active = data.get("active", True)
    typing = data.get("typing", False)
    if not isinstance(active, bool) or not isinstance(typing, bool):
        return jsonify({
            "success": False,
            "message": "Presence values must be true or false."
        }), 400

    typing_to_id = None
    typing_at = None
    if active and typing:
        try:
            typing_to_id = int(data.get("recipient_id"))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Select a valid conversation."
            }), 400
        if typing_to_id == user_id or find_chat_recipient(typing_to_id) is None:
            return jsonify({
                "success": False,
                "message": "That user is unavailable."
            }), 404
        typing_at = int(time.time())

    last_seen_at = int(time.time()) if active else 0
    with connect_database() as connection:
        connection.execute(
            """
            INSERT INTO chat_presence (
                user_id, last_seen_at, typing_to_id, typing_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                typing_to_id = excluded.typing_to_id,
                typing_at = excluded.typing_at
            """,
            (user_id, last_seen_at, typing_to_id, typing_at)
        )

    return jsonify({"success": True})


@app.route("/chat/messages", methods=["GET", "POST"])
def chat_messages():
    user_id = session.get("user_id")
    if user_id is None or get_chat_user(user_id) is None:
        session.clear()
        return jsonify({
            "success": False,
            "message": "Sign in to use chat."
        }), 401

    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "message": "A valid JSON request is required."
            }), 400

        try:
            recipient_id = int(data.get("recipient_id"))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Select a valid conversation."
            }), 400

        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({
                "success": False,
                "message": "Write a message before sending."
            }), 400
        if len(text) > 2000:
            return jsonify({
                "success": False,
                "message": "Messages must be 2,000 characters or fewer."
            }), 400
        if recipient_id == user_id:
            return jsonify({
                "success": False,
                "message": "You cannot send a direct message to yourself."
            }), 400

        recipient = find_chat_recipient(recipient_id)
        if recipient is None:
            return jsonify({
                "success": False,
                "message": "That user is unavailable."
            }), 404

        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect_database() as connection:
            cursor = connection.execute(
                """
                INSERT INTO direct_messages (
                    sender_id, recipient_id, body, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (user_id, recipient_id, text.strip(), created_at)
            )
            message_id = cursor.lastrowid

        sync_json_data()
        return jsonify({
            "success": True,
            "message": {
                "id": message_id,
                "sender_id": user_id,
                "recipient_id": recipient_id,
                "text": text.strip(),
                "created_at": created_at,
                "sent": True
            }
        }), 201

    try:
        recipient_id = int(request.args.get("with", ""))
        after_id = int(request.args.get("after", "0"))
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Select a valid conversation."
        }), 400

    if recipient_id == user_id or after_id < 0:
        return jsonify({
            "success": False,
            "message": "Select a valid conversation."
        }), 400
    if find_chat_recipient(recipient_id) is None:
        return jsonify({
            "success": False,
            "message": "That user is unavailable."
        }), 404

    now = int(time.time())
    with connect_database() as connection:
        if after_id == 0:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT id, sender_id, recipient_id, body, created_at
                    FROM direct_messages
                    WHERE
                        (sender_id = ? AND recipient_id = ?)
                        OR (sender_id = ? AND recipient_id = ?)
                    ORDER BY id DESC
                    LIMIT 100
                )
                ORDER BY id ASC
                """,
                (user_id, recipient_id, recipient_id, user_id)
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id, sender_id, recipient_id, body, created_at
                FROM direct_messages
                WHERE id > ?
                    AND (
                        (sender_id = ? AND recipient_id = ?)
                        OR (sender_id = ? AND recipient_id = ?)
                    )
                ORDER BY id ASC
                LIMIT 100
                """,
                (after_id, user_id, recipient_id, recipient_id, user_id)
            ).fetchall()
        presence = connection.execute(
            """
            SELECT last_seen_at, typing_to_id, typing_at
            FROM chat_presence
            WHERE user_id = ?
            """,
            (recipient_id,)
        ).fetchone()

    online = presence is not None and presence["last_seen_at"] >= now - 30
    is_typing = (
        online
        and presence["typing_to_id"] == user_id
        and presence["typing_at"] is not None
        and presence["typing_at"] >= now - 5
    )

    return jsonify({
        "success": True,
        "online": online,
        "is_typing": is_typing,
        "messages": [
            serialize_message(row, user_id)
            for row in rows
        ]
    })


@app.route("/banned")
def banned():
    return send_from_directory(
        os.path.join(BASE_DIR, "resources", "static", "sites"),
        "banned.html"
    )


@app.route("/ban-status")
def ban_status():
    ban = current_request_ban()
    if ban is None:
        return jsonify({"success": True, "banned": False})

    return jsonify({
        "success": True,
        "banned": True,
        "ban_type": ban["type"],
        "reason": ban["reason"]
    })


@app.route("/session", methods=["GET"])
def current_session():
    ban = current_request_ban()
    if ban:
        return json_ban_response(ban)

    user_id = session.get("user_id")
    if user_id is not None:
        with connect_database() as connection:
            user = connection.execute(
                """
                SELECT id, username, email, profile_photo_version,
                       profile_photo IS NOT NULL AS has_profile_photo
                FROM users
                WHERE id = ? AND verified = 1
                """,
                (user_id,)
            ).fetchone()

        if user is not None:
            return jsonify({
                "success": True,
                "authenticated": True,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "email": user["email"],
                    "profile_photo_version": user["profile_photo_version"],
                    "has_profile_photo": bool(user["has_profile_photo"]),
                    "is_admin": user["username"].casefold() == "koutroufinis"
                }
            })

        session.clear()

    pending_user_id = session.get("pending_user_id")
    pending_email = None
    if pending_user_id is not None:
        with connect_database() as connection:
            pending_user = connection.execute(
                "SELECT email FROM users WHERE id = ? AND verified = 0",
                (pending_user_id,)
            ).fetchone()
        if pending_user is not None:
            pending_email = pending_user["email"]
        else:
            session.clear()

    return jsonify({
        "success": True,
        "authenticated": False,
        "verification_required": pending_email is not None,
        "verification_email": pending_email
    })


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    username = data.get("username")
    password = data.get("password")
    remember_me = data.get("remember_me", False)

    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not isinstance(remember_me, bool)
        or not username.strip()
        or not password
    ):
        return jsonify({
            "success": False,
            "message": "Username and password are required."
        }), 400
    username = username.strip()

    with connect_database() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({
            "success": False,
            "message": "The username or password is incorrect."
        }), 401

    ban = find_ban(username=user["username"])
    if ban:
        return json_ban_response(ban)

    session.clear()
    if not user["verified"]:
        session["pending_user_id"] = user["id"]
        session["remember_me"] = remember_me
        return jsonify({
            "success": False,
            "verification_required": True,
            "message": "Verify your account before logging in."
        }), 403

    with connect_database() as connection:
        record_login(connection, user, request.remote_addr)
    sync_json_data()

    session["user_id"] = user["id"]
    session.permanent = remember_me
    return jsonify({
        "success": True,
        "message": f"Welcome back, {user['username']}!"
    })


@app.route("/control-panel")
def control_panel():
    if get_admin_user(session.get("user_id")) is None:
        return redirect("/interface" if session.get("user_id") else "/")
    return send_from_directory(
        os.path.join(BASE_DIR, "resources", "static", "sites"),
        "control-panel.html"
    )


@app.route("/admin/data")
def admin_data():
    _, error = require_admin()
    if error:
        return error

    filenames = {
        "users": "users.json",
        "login_history": "login_history.json",
        "messages": "messages.json",
        "banned": "banned.json"
    }
    data = {}
    for key, filename in filenames.items():
        path = os.path.join(JSON_DATA_DIR, filename)
        if not os.path.isfile(path):
            return jsonify({
                "success": False,
                "message": f"Unable to read {filename}."
            }), 500
        with open(path, encoding="utf-8") as data_file:
            data[key] = json.load(data_file)
    return jsonify({"success": True, "data": data})


@app.route("/admin/conversations/<int:other_user_id>")
def admin_conversation(other_user_id):
    _, error = require_admin()
    if error:
        return error

    if other_user_id == session.get("user_id"):
        return jsonify({
            "success": False,
            "message": "Choose another user to inspect."
        }), 400

    with connect_database() as connection:
        user = connection.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (other_user_id,)
        ).fetchone()
        if user is None:
            return jsonify({
                "success": False,
                "message": "That user does not exist."
            }), 404
        rows = connection.execute(
            """
            SELECT sender_id, recipient_id, body, created_at
            FROM direct_messages
            WHERE sender_id = ? OR recipient_id = ?
            ORDER BY id
            """,
            (other_user_id, other_user_id)
        ).fetchall()

    with connect_database() as connection:
        usernames = {
            row["id"]: row["username"]
            for row in connection.execute("SELECT id, username FROM users")
        }
    return jsonify({
        "success": True,
        "user": {"id": user["id"], "username": user["username"]},
        "messages": [
            {
                "sender_id": row["sender_id"],
                "sender": usernames.get(row["sender_id"], "Deleted user"),
                "recipient_id": row["recipient_id"],
                "recipient": usernames.get(row["recipient_id"], "Deleted user"),
                "text": row["body"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
    })


@app.route("/admin/bans", methods=["POST", "DELETE"])
def admin_bans():
    admin, error = require_admin()
    if error:
        return error

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    kind = data.get("kind")
    value = data.get("value")
    if not isinstance(value, str) or not value.strip():
        return jsonify({
            "success": False,
            "message": "Enter a username or IP address."
        }), 400
    value = value.strip()

    if kind == "username":
        if len(value) > 30:
            return jsonify({
                "success": False,
                "message": "Usernames must be 30 characters or fewer."
            }), 400
        with connect_database() as connection:
            target = connection.execute(
                "SELECT id, username FROM users WHERE username = ?",
                (value,)
            ).fetchone()
        if target is None and request.method != "DELETE":
            return jsonify({
                "success": False,
                "message": "That user does not exist."
            }), 404
        if target is not None:
            value = target["username"]
        if value.casefold() == admin["username"].casefold():
            return jsonify({
                "success": False,
                "message": "You cannot ban the control-panel administrator."
            }), 400
        key = "usernames"
        value_key = "username"
    elif kind == "ip_address":
        try:
            value = str(ipaddress.ip_address(value))
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Enter a valid IPv4 or IPv6 address."
            }), 400
        try:
            current_ip = str(ipaddress.ip_address(request.remote_addr))
        except ValueError:
            current_ip = request.remote_addr
        if request.method != "DELETE" and value == current_ip:
            return jsonify({
                "success": False,
                "message": "You cannot ban the IP address you are using."
            }), 400
        key = "ip_addresses"
        value_key = "ip_address"
    else:
        return jsonify({
            "success": False,
            "message": "Choose a username or IP address ban."
        }), 400

    with JSON_SYNC_LOCK:
        ban_list = load_ban_list()
        index = next(
            (
                index
                for index, entry in enumerate(ban_list[key])
                if isinstance(entry, dict)
                and str(entry.get(value_key, "")).casefold() == value.casefold()
            ),
            None
        )
        if request.method == "DELETE":
            if index is None:
                return jsonify({
                    "success": False,
                    "message": "That ban was not found."
                }), 404
            ban_list[key].pop(index)
        else:
            reason = data.get("reason", "")
            if not isinstance(reason, str) or len(reason.strip()) > 200:
                return jsonify({
                    "success": False,
                    "message": "Ban reasons must be 200 characters or fewer."
                }), 400
            ban = {value_key: value, "reason": reason.strip()}
            if index is None:
                ban_list[key].append(ban)
            else:
                ban_list[key][index] = ban
        write_json_snapshot(BAN_LIST_PATH, ban_list)

    return jsonify({
        "success": True,
        "banned": ban_list
    })


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    confirm_password = data.get("confirm_password")

    if (
        not isinstance(username, str)
        or not isinstance(email, str)
        or not isinstance(password, str)
        or not isinstance(confirm_password, str)
        or not username.strip()
        or not email.strip()
        or not password
    ):
        return jsonify({
            "success": False,
            "message": "All fields are required."
        }), 400

    username = username.strip()
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({
            "success": False,
            "message": "Enter a valid email address."
        }), 400

    if password != confirm_password:
        return jsonify({
            "success": False,
            "message": "Passwords do not match."
        }), 400

    ban = find_ban(username=username)
    if ban:
        return json_ban_response(ban)

    try:
        with connect_database() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (username, email, generate_password_hash(password))
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({
            "success": False,
            "message": "That username or email is already registered."
        }), 409

    code = generate_verification_code()
    try:
        send_verification_email(email, code)
    except (OSError, smtplib.SMTPException, ValueError):
        with connect_database() as connection:
            connection.execute(
                "DELETE FROM users WHERE id = ? AND verified = 0",
                (user_id,)
            )
        app.logger.error("Verification email could not be sent.")
        return jsonify({
            "success": False,
            "message": (
                "We couldn't send the verification email. "
                "Check the mail configuration and try again."
            )
        }), 503

    with connect_database() as connection:
        save_verification_code(connection, user_id, code)
    sync_json_data()

    session.clear()
    session["pending_user_id"] = user_id
    return jsonify({
        "success": True,
        "message": "A verification code was sent to your email."
    })


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request is required."
        }), 400

    code = data.get("code") or ""
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return jsonify({
            "success": False,
            "message": "Enter a valid 6-digit verification code."
        }), 400

    user_id = session.get("pending_user_id")
    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Start by creating an account or logging in."
        }), 401

    remember_me = session.get("remember_me", False)
    with connect_database() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE id = ? AND verified = 0",
            (user_id,)
        ).fetchone()

        if (
            user is None
            or user["verification_expires_at"] is None
            or user["verification_expires_at"] < int(time.time())
            or not check_password_hash(user["verification_code_hash"], code)
        ):
            return jsonify({
                "success": False,
                "message": "That code is invalid or expired. Request a new code."
            }), 400

        connection.execute(
            """
            UPDATE users
            SET verified = 1,
                verification_code_hash = NULL,
                verification_expires_at = NULL
            WHERE id = ?
            """,
            (user_id,)
        )
        record_login(connection, user, request.remote_addr)

    sync_json_data()
    session.clear()
    session["user_id"] = user_id
    session.permanent = remember_me is True
    return jsonify({
        "success": True,
        "message": "Your account is verified."
    })


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    user_id = session.get("pending_user_id")
    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Start by creating an account or logging in."
        }), 401

    with connect_database() as connection:
        user = connection.execute(
            "SELECT id, email FROM users WHERE id = ? AND verified = 0",
            (user_id,)
        ).fetchone()
        if user is None:
            session.clear()
            return jsonify({
                "success": False,
                "message": "There is no account waiting for verification."
            }), 404

    code = generate_verification_code()
    try:
        send_verification_email(user["email"], code)
    except (OSError, smtplib.SMTPException, ValueError):
        app.logger.error("Verification email could not be resent.")
        return jsonify({
            "success": False,
            "message": "We couldn't send a new verification email. Try again."
        }), 503

    with connect_database() as connection:
        save_verification_code(connection, user_id, code)

    return jsonify({
        "success": True,
        "message": "A new verification code was sent to your email."
    })


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({
        "success": True,
        "message": "You have been logged out."
    })


@app.route("/resources/<path:filename>")
def resources(filename):
    resource_path = filename.replace("\\", "/")
    if resource_path == "static/sites/interface.html":
        return redirect("/interface")
    if resource_path == "static/sites/control-panel.html":
        return redirect("/control-panel")

    return send_from_directory(
        os.path.join(BASE_DIR, "resources"),
        filename
    )

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
