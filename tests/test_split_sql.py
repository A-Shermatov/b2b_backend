from app.db.sql import split_sql


def test_split_sql_keeps_dollar_quoted_do_blocks_intact():
    sql = """
    DO $$
    BEGIN
        CREATE TYPE public.support_ticket_status AS ENUM ('open', 'closed');
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
    $$;

    ALTER TYPE public.notification_type ADD VALUE IF NOT EXISTS 'support';

    CREATE TABLE IF NOT EXISTS public.support_tickets (
        id integer PRIMARY KEY
    );
    """
    statements = split_sql(sql)
    assert len(statements) == 3
    assert statements[0].startswith("DO $$")
    assert "CREATE TYPE public.support_ticket_status" in statements[0]
    assert statements[0].rstrip().endswith("$$;")
    assert "ADD VALUE IF NOT EXISTS 'support'" in statements[1]
    assert "CREATE TABLE IF NOT EXISTS public.support_tickets" in statements[2]


def test_split_sql_ignores_semicolons_inside_quotes():
    sql = "INSERT INTO t(name) VALUES ('a;b'); SELECT 1;"
    assert split_sql(sql) == [
        "INSERT INTO t(name) VALUES ('a;b');",
        "SELECT 1;",
    ]
