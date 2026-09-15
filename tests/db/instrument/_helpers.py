"""db.instrument DAO 单测共享伪连接（迁移自 tests/dataflows/store/test_stock_daily_dao）。"""


class _FakeCopier:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        self.rows.append(row)


class _FakeCursor:
    """伪游标：记录 executed SQL，COPY 行写入 copier.rows。"""

    def __init__(self, rows=None, description=()):
        self.rows = rows or []
        self.description = description
        self.executed = []
        self.copier = None

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def copy(self, sql):
        self.executed.append(sql)
        self.copier = _FakeCopier()
        return self.copier

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Col:
    """伪列描述（psycopg description 元素，含 .name）。"""

    def __init__(self, name):
        self.name = name


class _FakeConn:
    """伪连接：conn.execute 按 SQL 子串匹配返回预设行集（查询函数用）；
    conn.cursor 返回共享伪游标（批量写入用）。"""

    def __init__(self, matchers=None):
        self.matchers = matchers or {}
        self.cursor_obj = _FakeCursor()
        self.commits = 0

    def execute(self, sql, params=None):
        rows = None
        # 逆序遍历：后插入的精确键优先（宽键可作 fallback）
        for key, r in reversed(list(self.matchers.items())):
            if key in sql:
                rows = r
                break
        self.cursor_obj.rows = rows if rows is not None else []
        self.cursor_obj.executed.append(sql)
        return self.cursor_obj

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass
