# -*- coding: utf-8 -*-
"""backend/auth.py：Token 签发/校验 + 审计日志"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

# 用户配置（环境变量覆盖，默认 admin/admin）
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")

_SECRET = os.environ.get("AUTH_SECRET", "") or secrets.token_hex(32)
_TOKEN_TTL = 12 * 3600  # 12 小时
_lock = threading.Lock()
_tokens = {}  # token -> {user, exp}

AUDIT_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "work", "logs", "audit.jsonl")


def _verify_password(user, password):
    return hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASS)


def login(username, password):
    """登录成功返回 token；失败返回 None"""
    if not username or not password:
        return None
    if not _verify_password(username.strip(), password):
        return None
    token = secrets.token_hex(24)
    with _lock:
        _tokens[token] = {"user": username.strip(), "exp": time.time() + _TOKEN_TTL}
    audit(username.strip(), "login", "POST /api/login", 200)
    return token


def check_token(token):
    if not token:
        return None
    with _lock:
        rec = _tokens.get(token)
        if not rec:
            return None
        if rec["exp"] < time.time():
            _tokens.pop(token, None)
            return None
        return rec["user"]


def logout(token):
    with _lock:
        _tokens.pop(token, None)


def audit(user, action, path, status):
    """追加审计日志（失败不阻断请求）"""
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "user": user, "action": action, "path": path, "status": status}
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
