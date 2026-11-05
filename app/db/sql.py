from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _dollar_tag_at(sql: str, index: int) -> str | None:
    """Return dollar-quote tag starting at index (e.g. '$$' or '$tag$'), else None."""
    if index >= len(sql) or sql[index] != "$":
        return None
    end = index + 1
    while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
        end += 1
    if end < len(sql) and sql[end] == "$":
        return sql[index : end + 1]
    return None


def split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements, respecting quotes and dollar-quotes."""
    statements: list[str] = []
    current: list[str] = []
    i = 0
    length = len(sql)
    in_single = False
    in_double = False
    dollar_tag: str | None = None
    line_start = True
    line_is_meta = False

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if ch == "\n":
            if current and not line_is_meta:
                current.append(ch)
            line_start = True
            line_is_meta = False
            i += 1
            continue

        if line_start:
            # Skip psql meta-commands like \restrict
            j = i
            while j < length and sql[j] in " \t":
                j += 1
            if j < length and sql[j] == "\\":
                line_is_meta = True
                while i < length and sql[i] != "\n":
                    i += 1
                continue
            # Skip full-line SQL comments
            if j + 1 < length and sql[j : j + 2] == "--":
                while i < length and sql[i] != "\n":
                    i += 1
                continue
            line_start = False

        if line_is_meta:
            i += 1
            continue

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(ch)
                i += 1
            continue

        if in_single:
            current.append(ch)
            if ch == "'" and nxt == "'":
                current.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            current.append(ch)
            if ch == '"' and nxt == '"':
                current.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            while i < length and sql[i] != "\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, length)
            continue

        tag = _dollar_tag_at(sql, i)
        if tag is not None:
            dollar_tag = tag
            current.append(tag)
            i += len(tag)
            continue

        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue

        current.append(ch)
        if ch == ";":
            stmt = "".join(current).strip()
            current = []
            if stmt:
                statements.append(stmt)
        i += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


async def execute_sql_script(conn, sql: str) -> None:
    for statement in split_sql(sql):
        await conn.execute(statement)


async def execute_sql_file(conn, path: Path) -> None:
    await execute_sql_script(conn, path.read_text(encoding="utf-8"))


def like_contains(query: str, max_length: int = 200) -> str:
    """Pattern for ILIKE '%...%' that treats the user's text literally.

    % and _ typed by the user are escaped (PostgreSQL's default LIKE escape is a
    backslash), and very long queries are cut.
    """
    text = (query or "")[:max_length]
    text = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{text}%"
