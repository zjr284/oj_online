"""安全工具：密码哈希与令牌生成。

密码使用标准库 PBKDF2-SHA256（零额外依赖），存储格式：
pbkdf2$<迭代次数>$<盐hex>$<摘要hex>
"""
import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 600_000  # OWASP 对 PBKDF2-SHA256 的推荐值


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
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
