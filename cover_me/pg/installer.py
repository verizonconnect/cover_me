"""
Install/uninstall instrumented functions and helper functions in the database.
"""
from pathlib import Path
from cover_me.models import ProcedureDef, load_cached_source, load_cached_meta


# Helper functions installed in cover_me schema for coverage tracking
HELPER_SQL = """
CREATE SCHEMA IF NOT EXISTS cover_me;

CREATE OR REPLACE FUNCTION cover_me.cond(message VARCHAR, value BOOLEAN)
  RETURNS BOOLEAN AS $$
BEGIN
  IF value THEN
    RAISE WARNING 'COVER_ME % t', message;
  ELSE
    RAISE WARNING 'COVER_ME % f', message;
  END IF;
  RETURN value;
END $$ LANGUAGE plpgsql VOLATILE;

CREATE OR REPLACE FUNCTION cover_me.branch(message VARCHAR)
  RETURNS VOID AS $$
BEGIN
  RAISE WARNING 'COVER_ME %', message;
END $$ LANGUAGE plpgsql VOLATILE;

CREATE OR REPLACE FUNCTION cover_me.signal(message VARCHAR, signal VARCHAR)
  RETURNS VOID AS $$
BEGIN
  RAISE WARNING 'COVER_ME % %', message, signal;
END $$ LANGUAGE plpgsql VOLATILE;

REVOKE EXECUTE ON FUNCTION cover_me.cond(VARCHAR, BOOLEAN) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION cover_me.branch(VARCHAR) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION cover_me.signal(VARCHAR, VARCHAR) FROM PUBLIC;
"""

DROP_HELPERS_SQL = """
DROP FUNCTION IF EXISTS cover_me.cond(VARCHAR, BOOLEAN);
DROP FUNCTION IF EXISTS cover_me.branch(VARCHAR);
DROP FUNCTION IF EXISTS cover_me.signal(VARCHAR, VARCHAR);
DROP SCHEMA IF EXISTS cover_me CASCADE;
"""


def _build_create_function_sql(proc: ProcedureDef, body: str) -> str:
    """Build a CREATE OR REPLACE FUNCTION/PROCEDURE statement."""
    # Use the full arg string from pg_get_function_arguments when available
    # (preserves DEFAULT values which CREATE OR REPLACE requires to match)
    if proc.arg_defaults:
        args = proc.arg_defaults
    else:
        # Separate input params from TABLE (RETURNS TABLE) columns
        in_parts = []
        table_parts = []
        for m, n, t in zip(proc.arg_modes, proc.arg_names, proc.arg_types):
            if m == "TABLE":
                table_parts.append(f"{n} {t}")
            else:
                in_parts.append(f"{m} {n} {t}")
        args = ", ".join(in_parts)

    if proc.is_procedure:
        parts = [f"CREATE OR REPLACE PROCEDURE {proc.qualified_name}({args})"]
        parts.append("  AS $COVER_ME$")
        parts.append(body)
        parts.append("$COVER_ME$ LANGUAGE plpgsql")
        if proc.is_secdef:
            parts.append("  SECURITY DEFINER")
        return "\n".join(parts) + ";"
    else:
        # Build RETURNS clause from TABLE columns if present
        if not proc.arg_defaults:
            table_parts = []
            for m, n, t in zip(proc.arg_modes, proc.arg_names, proc.arg_types):
                if m == "TABLE":
                    table_parts.append(f"{n} {t}")
        else:
            table_parts = [
                f"{n} {t}" for m, n, t in zip(proc.arg_modes, proc.arg_names, proc.arg_types)
                if m == "TABLE"
            ]

        if table_parts:
            returns = f"TABLE({', '.join(table_parts)})"
        elif proc.is_setof:
            returns = f"SETOF {proc.return_type}"
        else:
            returns = proc.return_type

        parts = [f"CREATE OR REPLACE FUNCTION {proc.qualified_name}({args})"]
        parts.append(f"  RETURNS {returns} AS $COVER_ME$")
        parts.append(body)
        parts.append("$COVER_ME$ LANGUAGE plpgsql")
        if proc.volatility:
            parts.append(f"  {proc.volatility}")
        if proc.is_strict:
            parts.append("  STRICT")
        if proc.is_secdef:
            parts.append("  SECURITY DEFINER")
        return "\n".join(parts) + ";"


def install_helpers(connection) -> None:
    """Install cover_me helper functions."""
    with connection.cursor() as cur:
        cur.execute(HELPER_SQL)
    connection.commit()


def uninstall_helpers(connection) -> None:
    """Remove cover_me helper functions."""
    with connection.cursor() as cur:
        cur.execute(DROP_HELPERS_SQL)
    connection.commit()


def install_instrumented(connection, proc: ProcedureDef, instrumented_source: str) -> bool:
    """Replace a function with its instrumented version. Returns True on success."""
    sql = _build_create_function_sql(proc, instrumented_source)
    with connection.cursor() as cur:
        try:
            cur.execute(sql)
            connection.commit()
            return True
        except Exception as e:
            connection.rollback()
            print(f"  WARNING: Could not instrument {proc.qualified_name}: {e}", flush=True)
            return False


def restore_original(connection, proc_oid: str, cache_dir: Path) -> bool:
    """Restore a function from cached original source. Returns True on success."""
    source = load_cached_source(proc_oid, cache_dir)
    meta = load_cached_meta(proc_oid, cache_dir)
    if source is None or meta is None:
        return False

    proc = ProcedureDef(
        oid=meta["oid"],
        schema=meta["schema"],
        name=meta["name"],
        source=source,
        is_strict=meta["is_strict"],
        is_secdef=meta["is_secdef"],
        is_setof=meta["is_setof"],
        is_procedure=meta.get("is_procedure", False),
        return_type=meta["return_type"],
        volatility=meta["volatility"],
        arg_modes=meta["arg_modes"],
        arg_names=meta["arg_names"],
        arg_types=meta["arg_types"],
        arg_defaults=meta.get("arg_defaults"),
    )

    sql = _build_create_function_sql(proc, source)
    with connection.cursor() as cur:
        try:
            cur.execute(sql)
            connection.commit()
        except Exception as e:
            connection.rollback()
            print(f"  WARNING: Could not restore {proc.qualified_name}: {e}", flush=True)
            return False
    return True
