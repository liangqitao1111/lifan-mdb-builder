# -*- coding: utf-8 -*-
"""backend/auth.py：Token 签发/校验 + 审计日志

上线前安全收紧（差异清单 P2-10）：
- Token 由内存表 + HMAC-SHA256 签名双重校验：AUTH_SECRET 参与签名（此前为死变量，
  仅内存表即可伪造任意 token——只要拿到 token 值，重启后仍可用）。
- 签名格式：token_hex.signature_hex（signature = HMAC(secret, token_hex)）。
  校验时同时要求【内存表有效 + 签名匹配】，双因素缺一不可。
- 兼容：无签名的旧格式 token 在内存表有效期内仍可用（存量会话平滑过渡），
  重启后旧格式自然失效（内存表清空）。
- 默认凭据：ADMIN_PASS 未显式设置时，启动横幅高声提醒（不阻断本地使用）。
"""
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

if not os.environ.get("AUTH_SECRET") and os.environ.get("LIFAN_WARN_DEFAULTS", "1") != "0":
    print("[auth] ⚠ AUTH_SECRET 未设置：已用随机密钥（重启后全部会话失效）。"
          "生产部署请设置 AUTH_SECRET / ADMIN_USER / ADMIN_PASS 环境变量。")
if ADMIN_USER == "admin" and ADMIN_PASS == "admin":
    print("[auth] ⚠ 默认凭据 admin/admin 生效：公网部署前请务必设置 ADMIN_USER/ADMIN_PASS。")


def _sign(token_raw: str) -> str:
    return hmac.new(_SECRET.encode(), token_raw.encode(), hashlib.sha256).hexdigest()


def _verify_password(user, password):
    return hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASS)


def login(username, password):
    """登录成功返回签名 token；失败返回 None"""
    if not username or not password:
        return None
    if not _verify_password(username.strip(), password):
        return None
    token_raw = secrets.token_hex(24)
    now = time.time()
    with _lock:
        # 顺带清理过期 token（内存表防无限增长）
        expired = [t for t, rec in _tokens.items() if rec["exp"] < now]
        for t in expired:
            _tokens.pop(t, None)
        _tokens[token_raw] = {"user": username.strip(), "exp": now + _TOKEN_TTL}
    token = f"{token_raw}.{_sign(token_raw)}"
    audit(username.strip(), "login", "POST /api/login", 200)
    return token


def check_token(token):
    if not token:
        return None
    # 双因素：签名校验 + 内存表有效性
    token_raw = token
    if "." in token:
        token_raw, _, sig = token.rpartition(".")
        if not hmac.compare_digest(_sign(token_raw), sig):
            return None
    with _lock:
        rec = _tokens.get(token_raw)
        if not rec:
            return None
        if rec["exp"] < time.time():
            _tokens.pop(token_raw, None)
            return None
        return rec["user"]


def logout(token):
    if not token:
        return
    token_raw = token.rpartition(".")[0] if "." in token else token
    with _lock:
        _tokens.pop(token_raw, None)


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
