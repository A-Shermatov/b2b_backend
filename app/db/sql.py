from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("\\"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip()
            current = []
            useful = [
                part
                for part in stmt.splitlines()
                if part.strip() and not part.strip().startswith("--")
            ]
            if useful:
                statements.append(stmt)
    return statements


async def execute_sql_script(conn, sql: str) -> None:
    for statement in split_sql(sql):
        await conn.execute(statement)


async def execute_sql_file(conn, path: Path) -> None:
    await execute_sql_script(conn, path.read_text(encoding="utf-8"))
