"""安全工具：密码哈希与令牌生成。

密码使用 bcrypt 哈希（Step 4 要求），存储格式为标准 bcrypt 串（$2b$...）。
兼容旧版 PBKDF2 格式（pbkdf2$...），用于已存在的数据，新密码一律 bcrypt。
"""
import hashlib
import hmac
import secrets

import bcrypt

# bcrypt 输入上限 72 字节，超长密码截断（不影响本课程场景）
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("$2"):   # bcrypt 格式
        try:
            return bcrypt.checkpw(password.encode()[:_BCRYPT_MAX_BYTES], stored.encode())
        except ValueError:
            return False
    return _verify_legacy_pbkdf2(password, stored)


def _verify_legacy_pbkdf2(password: str, stored: str) -> bool:
    """旧版 PBKDF2-SHA256 格式（保留兼容，新数据不再使用）。"""
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    """生成随机会话令牌。"""
    return secrets.token_hex(32)
