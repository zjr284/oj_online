"""分页参数的公共安全边界。

限制单页数量和页码，避免过大整数传入 SQLite LIMIT/OFFSET
导致 500，也避免单次请求加载过多记录。
"""

MAX_PAGE = 10_000_000
MAX_PAGE_SIZE = 100
