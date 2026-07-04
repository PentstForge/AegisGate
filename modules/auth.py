import json
import os
import hashlib
import hmac
import time
import secrets

AUTH_FILE = "/opt/nft-dashboard/data/auth.json"
SESSION_FILE = "/var/log/ram/sessions.json"
SESSION_TTL = 86400


def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"


def _verify_password(password, stored):
    try:
        salt, hashed = stored.split("$", 1)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return hmac.compare_digest(computed.hex(), hashed)
    except Exception:
        return False


def init_auth():
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    if not os.path.exists(AUTH_FILE):
        default_pass = secrets.token_urlsafe(12)
        hashed = _hash_password(default_pass)
        with open(AUTH_FILE, "w") as f:
            json.dump({"username": "admin", "password": hashed}, f, indent=2)
        os.chmod(AUTH_FILE, 0o600)
        return default_pass
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass
    return None


def authenticate(username, password):
    try:
        with open(AUTH_FILE, "r") as f:
            auth = json.load(f)
    except Exception:
        return False
    if auth.get("username") != username:
        return False
    return _verify_password(password, auth.get("password", ""))


def create_session(username):
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    try:
        with open(SESSION_FILE, "r") as f:
            sessions = json.load(f)
    except Exception:
        sessions = {}
    now = time.time()
    sessions = {k: v for k, v in sessions.items() if v.get("ts", 0) > now - SESSION_TTL}
    sessions[token] = {"username": username, "ts": now}
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f)
    try:
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass
    return token


def verify_session(token):
    if not token:
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            sessions = json.load(f)
    except Exception:
        return None
    session = sessions.get(token)
    if not session:
        return None
    if time.time() - session.get("ts", 0) > SESSION_TTL:
        sessions.pop(token, None)
        with open(SESSION_FILE, "w") as f:
            json.dump(sessions, f)
        return None
    return session.get("username")


def destroy_session(token):
    try:
        with open(SESSION_FILE, "r") as f:
            sessions = json.load(f)
        sessions.pop(token, None)
        with open(SESSION_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception:
        pass


def change_password(username, old_password, new_password):
    if not authenticate(username, old_password):
        return False, "Invalid current password"
    hashed = _hash_password(new_password)
    with open(AUTH_FILE, "w") as f:
        json.dump({"username": username, "password": hashed}, f, indent=2)
    os.chmod(AUTH_FILE, 0o600)
    return True, "Password changed successfully"


def get_auth_info():
    try:
        with open(AUTH_FILE, "r") as f:
            auth = json.load(f)
        return {"username": auth.get("username", "admin"), "has_password": bool(auth.get("password"))}
    except Exception:
        return {"username": "admin", "has_password": False}