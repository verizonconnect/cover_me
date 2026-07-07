# cover_me — Database Code Coverage

Code coverage for PostgreSQL PL/pgSQL and MySQL stored procedures and functions. Generates OpenCover XML reports and self-contained HTML reports.

Inspired by [piggly](https://github.com/kputnam/piggly) by kputnam — the original PL/pgSQL coverage tool written in Ruby.

## Quick Start

### Prerequisites

- Python 3.10+
- A running PostgreSQL or MySQL instance with stored procedures/functions to cover

### Install

```bash
# PostgreSQL only
pip install "cover_me[postgres] @ git+https://github.com/verizonconnect/cover_me.git"

# MySQL only
pip install "cover_me[mysql] @ git+https://github.com/verizonconnect/cover_me.git"

# Both engines
pip install "cover_me[all] @ git+https://github.com/verizonconnect/cover_me.git"
```

Or use Docker:

```bash
docker build -t cover_me .
```

### 30-Second Example (Postgres)

```bash
# Instrument → test → report → restore
cover_me trace   -E postgres -H localhost -d mydb -U postgres -W secret
pg_prove --host localhost --dbname mydb -r ./tests/ 2> ./coverage/trace.txt
cover_me report  -E postgres -H localhost -d mydb -U postgres -W secret -f ./coverage/trace.txt
cover_me untrace -E postgres -H localhost -d mydb -U postgres -W secret

# Open coverage/html/index.html in your browser
```

### 30-Second Example (MySQL)

```bash
# Instrument → test → report → restore
cover_me trace   -E mysql -H localhost -p 3306 -d mydb -U root -W secret
my_prove -h localhost -u root -psecret -D mydb --ext .sql -r ./tests/
cover_me report  -E mysql -H localhost -p 3306 -d mydb -U root -W secret
cover_me untrace -E mysql -H localhost -p 3306 -d mydb -U root -W secret

# Open coverage/html/index.html in your browser
```

---

## How It Works

cover_me instruments stored procedures and functions by injecting coverage tracking calls at branch, block, and loop points. When unit tests exercise the instrumented code, coverage hits are recorded. A report is then generated showing which lines and branches were executed.

```
trace → unit_test (external) → report → untrace
```

### Pipeline

1. **trace** — Connects to the database, queries all user-defined functions, caches their original source, installs helper functions, then replaces each function with an instrumented version containing coverage tracking calls.

2. **unit_test** (external — you run this) — Any test runner or application that exercises the instrumented functions. For Postgres, stderr must be captured to a file. For MySQL, hits are recorded automatically in a trace table.

3. **report** — Re-instruments the cached source to regenerate deterministic tag IDs, reads coverage hits (from file or table), and produces OpenCover XML + HTML reports.

4. **untrace** — Restores every function to its exact original definition from cache and removes helper functions.

### What Gets Instrumented

| Construct          | Tag Type | What's Tracked                    |
| ------------------ | -------- | --------------------------------- |
| `BEGIN`            | Block    | Entry into block                  |
| `IF` / `ELSIF`     | Branch   | Condition evaluated               |
| `ELSE`             | Branch   | Entry into else block             |
| `WHILE`            | Loop     | Condition evaluated               |
| `FOR` / `LOOP`     | Loop     | Entry into loop body              |
| `EXIT` / `CONTINUE`| Branch   | Statement reached                 |
| `RETURN`           | Branch   | Statement reached                 |
| `RAISE EXCEPTION`  | Branch   | Statement reached                 |
| `EXCEPTION WHEN`   | Block    | Handler entered                   |

Tag IDs are deterministic — `md5(oid:line:keyword)[:16]` — so the same source always produces the same tags regardless of when or where you run it.

### Engine-Specific Behaviour

| Aspect              | Postgres                                    | MySQL                                        |
| -------------------- | ------------------------------------------- | -------------------------------------------- |
| Trace mechanism      | `RAISE WARNING` (survives ROLLBACK)         | `INSERT INTO cover_me.trace` (MyISAM — survives ROLLBACK) |
| Condition wrapper    | `cover_me.cond(tag, condition)` function    | `cover_me.cover_me_cond(tag, condition)` function |
| Branch tracker       | `PERFORM cover_me.branch(tag)`              | `INSERT INTO cover_me.trace (tag_id) VALUES (tag)` |
| Source query         | `pg_proc.prosrc`                            | `information_schema.ROUTINES.ROUTINE_DEFINITION` |
| Replace mechanism    | `CREATE OR REPLACE FUNCTION`                | `DROP` + `CREATE` (via cached `SHOW CREATE`) |
| Helper location      | `cover_me` schema (EXECUTE revoked from PUBLIC) | `cover_me` database                     |
| Trace capture        | stderr file (`2> trace.txt`)                | MyISAM trace table (queried directly)        |
| DECLARE handling     | Injection after `BEGIN`                     | Injection after all `DECLARE` statements     |
| Function filter      | VOLATILE only (STABLE/IMMUTABLE excluded)   | All functions                                |

---

## CLI Reference

```bash
cover_me <command> [options]
```

### Commands

| Command   | Description                                          |
| --------- | ---------------------------------------------------- |
| `trace`   | Dump, instrument, and install functions               |
| `report`  | Parse trace data and generate OpenCover XML + HTML    |
| `untrace` | Restore original functions and remove helpers         |

### Options

| Flag              | Description                              | Default              |
| ----------------- | ---------------------------------------- | -------------------- |
| `-E`, `--engine`  | Database engine (`postgres` or `mysql`)  | `postgres`           |
| `-H`, `--host`    | Database host                            | `localhost`          |
| `-p`, `--port`    | Database port                            | 5432 (pg) / 3306 (my) |
| `-d`, `--dbname`  | Database name                            | **required**         |
| `-U`, `--user`    | Database user                            | `root`               |
| `-W`, `--password`| Database password                        | empty                |
| `-c`, `--cache-dir`| Cache directory for original source     | `/coverage/cache`    |
| `-f`, `--file`    | Trace file path (report, Postgres only)  | none                 |
| `-o`, `--output`  | Output path for OpenCover XML            | `/coverage/opencover.xml` |
| `-x`, `--exclude` | Comma-separated schemas to exclude (additive to .cover_me config) | none        |
| `--config`        | Path to .cover_me config file             | `.cover_me` in CWD  |

---

## Running on PostgreSQL

### How Postgres Tracing Works

cover_me installs helper functions in a dedicated `cover_me` schema (with `EXECUTE` revoked from `PUBLIC`):

- `cover_me.branch(tag TEXT)` — emits `RAISE WARNING 'COVER_ME %', tag`
- `cover_me.cond(tag TEXT, cond BOOLEAN)` — emits `RAISE WARNING 'COVER_ME % t/f', tag` and returns the condition value

Because `RAISE WARNING` writes to stderr and is not transactional, coverage data survives even when tests use `ROLLBACK` (as pgTAP does).

Only VOLATILE functions and procedures are instrumented. STABLE and IMMUTABLE functions are excluded automatically — they are typically inlined by the planner and do not represent testable procedural logic.

### Step-by-Step: Postgres with pgTAP

```bash
# 1. Start a Postgres container (or use an existing instance)
docker run -d --name pg_cover \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=adventure_works \
    -p 5432:5432 \
    postgres:16-alpine

# 2. Deploy your schema and functions (e.g. via flyway, psql, etc.)
psql -h localhost -U postgres -d adventure_works -f schema.sql
psql -h localhost -U postgres -d adventure_works -f functions.sql

# 3. Instrument all functions for coverage
cover_me trace -E postgres \
    -H localhost -p 5432 -d adventure_works \
    -U postgres -W postgres \
    -c ./coverage/cache

# Output:
#   Found 14 functions
#   Installed helper functions
#     Traced common.get_stock (3 tags)
#     Traced common.get_product_dealer_price (5 tags)
#     ...
#   Instrumented 14 functions

# 4. Run your tests — capture stderr to a file
#    pgTAP with pg_prove:
pg_prove --host localhost --port 5432 \
    --username postgres --dbname adventure_works \
    --ext .sql --recurse ./tests/ \
    2> ./coverage/trace.txt

#    Or plain psql:
psql -h localhost -U postgres -d adventure_works \
    -f ./tests/run_all.sql \
    2> ./coverage/trace.txt

#    Or your application:
PGPASSWORD=postgres ./my_app --run-integration-tests 2> ./coverage/trace.txt

# 5. Generate the coverage report
cover_me report -E postgres \
    -H localhost -p 5432 -d adventure_works \
    -U postgres -W postgres \
    -c ./coverage/cache \
    -f ./coverage/trace.txt \
    -o ./coverage/opencover.xml

# Output:
#   Parsed 847 coverage hits from ./coverage/trace.txt
#   Exported source to coverage/source
#   Generated ./coverage/opencover.xml

# 6. Restore original functions
cover_me untrace -E postgres \
    -H localhost -p 5432 -d adventure_works \
    -U postgres -W postgres \
    -c ./coverage/cache

# Output:
#   Restored OID 16384
#   Restored OID 16385
#   ...
#   Restored 14 functions, removed helpers

# 7. View the report
open ./coverage/html/index.html
```

### Postgres: Important Notes

- **stderr capture is required** — The trace file is just the stderr output from whatever process exercises the functions. If you forget `2> trace.txt`, you'll get 0% coverage.
- **Multiple test runs** — You can append multiple runs to the same trace file (`2>> trace.txt`) before generating the report.
- **pgTAP compatibility** — pgTAP wraps each test in a transaction that rolls back. `RAISE WARNING` is not transactional, so all coverage hits are preserved.
- **Schema filtering** — Use a `.cover_me` TOML config file in your repo root to exclude schemas from instrumentation. The file uses a `[excluded_schemas]` section with a `names` array. The `-x` CLI flag is additive to the config file. Only `pg_*`, `information_schema`, and `cover_me` are excluded by default — all other exclusions (including `public`, `pgtap`, `tap`, extension schemas) are the responsibility of the repo owner. Only VOLATILE functions and procedures are instrumented; STABLE and IMMUTABLE functions are always excluded.
- **Security** — Helper functions are installed in a dedicated `cover_me` schema with `EXECUTE` revoked from `PUBLIC`. The superuser running the trace has implicit access. This avoids tripping permission-auditing tests (e.g. checks that `PUBLIC` has no function access outside whitelisted schemas).
- **Concurrent connections** — Multiple connections can exercise instrumented functions simultaneously. All `RAISE WARNING` output goes to the connection's stderr, so ensure all connections' stderr is captured.

### Postgres: Docker Compose Integration

```yaml
services:
    cover_trace:
        build: ./path/to/cover_me
        command: ["trace", "-E", "postgres", "-H", "db", "-p", "5432", "-d", "mydb", "-U", "postgres", "-W", "secret", "-c", "/coverage/cache"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            flyway:
                condition: service_completed_successfully

    unit_test:
        image: my-pgtap-image
        command: >
            bash -c "pg_prove --host db --username postgres --dbname mydb
            --ext .sql --recurse /tests/
            2> /coverage/trace.txt"
        volumes:
            - ./tests:/tests
            - ./coverage:/coverage
        depends_on:
            cover_trace:
                condition: service_completed_successfully

    cover_report:
        build: ./path/to/cover_me
        command: ["report", "-E", "postgres", "-H", "db", "-p", "5432", "-d", "mydb", "-U", "postgres", "-W", "secret", "-c", "/coverage/cache", "-f", "/coverage/trace.txt", "-o", "/coverage/opencover.xml"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            unit_test:
                condition: service_completed_successfully

    cover_untrace:
        build: ./path/to/cover_me
        command: ["untrace", "-E", "postgres", "-H", "db", "-p", "5432", "-d", "mydb", "-U", "postgres", "-W", "secret", "-c", "/coverage/cache"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            cover_report:
                condition: service_completed_successfully
```

---

## Running on MySQL

### How MySQL Tracing Works

cover_me creates a `cover_me` database containing:

- `cover_me.trace` — A MyISAM table that records tag hits. MyISAM is used because it does not support transactions, so inserts survive `ROLLBACK`.
- `cover_me.cover_me_cond(tag VARCHAR(16), cond BOOLEAN)` — Records the tag hit and returns the condition value.

Each instrumented branch/block/loop point inserts a row into `cover_me.trace`. The report command reads this table directly — no file capture needed.

### Step-by-Step: MySQL with MyTAP

```bash
# 1. Start a MySQL container (or use an existing instance)
docker run -d --name my_cover \
    -e MYSQL_ROOT_PASSWORD=root \
    -e MYSQL_DATABASE=adventure_works \
    -p 3306:3306 \
    mysql:8.4

# 2. Deploy your schema and functions
mysql -h localhost -u root -proot adventure_works < schema.sql
mysql -h localhost -u root -proot adventure_works < functions.sql

# 3. Instrument all functions for coverage
cover_me trace -E mysql \
    -H localhost -p 3306 -d adventure_works \
    -U root -W root \
    -c ./coverage/cache

# Output:
#   Found 14 functions
#   Installed helper functions
#     Traced common.get_stock (3 tags)
#     Traced common.get_product_dealer_price (5 tags)
#     ...
#   Instrumented 14 functions

# 4. Run your tests — no stderr capture needed!
#    MyTAP with my_prove:
my_prove -h localhost -u root -proot \
    -D adventure_works --ext .sql -r ./tests/

#    Or plain mysql client:
mysql -h localhost -u root -proot adventure_works < ./tests/run_all.sql

#    Or your application:
./my_app --run-integration-tests

# 5. Generate the coverage report (reads from cover_me.trace table)
cover_me report -E mysql \
    -H localhost -p 3306 -d adventure_works \
    -U root -W root \
    -c ./coverage/cache \
    -o ./coverage/opencover.xml

# Output:
#   Parsed 523 coverage hits from trace table
#   Exported source to coverage/source
#   Generated ./coverage/opencover.xml

# 6. Restore original functions
cover_me untrace -E mysql \
    -H localhost -p 3306 -d adventure_works \
    -U root -W root \
    -c ./coverage/cache

# Output:
#   Restored OID common.get_stock
#   Restored OID common.get_product_dealer_price
#   ...
#   Restored 14 functions, removed helpers

# 7. View the report
open ./coverage/html/index.html
```

### MySQL: Important Notes

- **No file capture needed** — Unlike Postgres, MySQL coverage hits are stored in the `cover_me.trace` MyISAM table. The report command reads directly from it.
- **log-bin-trust-function-creators** — If binary logging is enabled, you may need `SET GLOBAL log_bin_trust_function_creators = 1` or start MySQL with `--log-bin-trust-function-creators=1` to allow function creation without SUPER privilege.
- **DROP + CREATE** — MySQL doesn't support `CREATE OR REPLACE FUNCTION` for stored functions. cover_me caches the full `SHOW CREATE FUNCTION` output and uses `DROP` + `CREATE` to replace and restore functions.
- **Multiple databases** — cover_me instruments functions across all user databases in the instance (excluding system databases). The `-d` flag specifies the connection database but all schemas are scanned.
- **MyTAP compatibility** — MyTAP uses transactions for test isolation. Since the trace table is MyISAM (non-transactional), all coverage hits persist regardless of rollbacks.
- **DECLARE handling** — MySQL requires all `DECLARE` statements at the top of a `BEGIN...END` block. cover_me correctly injects instrumentation after the last `DECLARE` rather than immediately after `BEGIN`.

### MySQL: Docker Compose Integration

```yaml
services:
    cover_trace:
        build: ./path/to/cover_me
        command: ["trace", "-E", "mysql", "-H", "db", "-p", "3306", "-d", "mydb", "-U", "root", "-W", "secret", "-c", "/coverage/cache"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            flyway:
                condition: service_completed_successfully

    unit_test:
        image: my-mytap-image
        command: >
            bash -c "my_prove -h db -u root -psecret
            -D mydb --ext .sql -r /tests/"
        volumes:
            - ./tests:/tests
        depends_on:
            cover_trace:
                condition: service_completed_successfully

    cover_report:
        build: ./path/to/cover_me
        command: ["report", "-E", "mysql", "-H", "db", "-p", "3306", "-d", "mydb", "-U", "root", "-W", "secret", "-c", "/coverage/cache", "-o", "/coverage/opencover.xml"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            unit_test:
                condition: service_completed_successfully

    cover_untrace:
        build: ./path/to/cover_me
        command: ["untrace", "-E", "mysql", "-H", "db", "-p", "3306", "-d", "mydb", "-U", "root", "-W", "secret", "-c", "/coverage/cache"]
        volumes:
            - ./coverage:/coverage
        depends_on:
            cover_report:
                condition: service_completed_successfully
```

---

## Output

### OpenCover XML

`coverage/opencover.xml` — compatible with:
- [ReportGenerator](https://github.com/danielpalme/ReportGenerator)
- SonarQube
- Azure DevOps

OpenCover mapping:

| OpenCover Element | Maps To                    |
| ----------------- | -------------------------- |
| Module            | Schema / Database          |
| Class             | Schema.FunctionName        |
| Method            | The function itself        |
| SequencePoint     | Each instrumented point    |
| BranchPoint       | Each conditional (IF/WHILE)|

### HTML Report

`coverage/html/index.html` — self-contained, no external dependencies:
- Summary table with coverage percentage bars per function
- Per-function drill-down with green (hit) / red (miss) line highlighting
- Sequence and branch coverage percentages

### Source Export

`coverage/source/<schema>/<name>.sql` — original function source files, exported for ReportGenerator compatibility.

---

## CI/CD Integration

### GitHub Actions

```yaml
jobs:
  db-coverage:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: mydb
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready"
          --health-interval 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install cover_me
        run: pip install "cover_me[postgres] @ git+https://github.com/verizonconnect/cover_me.git"

      - name: Deploy schema
        run: psql -h localhost -U postgres -d mydb -f schema.sql
        env:
          PGPASSWORD: postgres

      - name: Instrument functions
        run: cover_me trace -E postgres -H localhost -d mydb -U postgres -W postgres -c ./coverage/cache

      - name: Run tests
        run: pg_prove --host localhost --dbname mydb -U postgres -r ./tests/ 2> ./coverage/trace.txt
        env:
          PGPASSWORD: postgres

      - name: Generate coverage report
        run: cover_me report -E postgres -H localhost -d mydb -U postgres -W postgres -c ./coverage/cache -f ./coverage/trace.txt -o ./coverage/opencover.xml

      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: db-coverage
          path: coverage/
```

### SonarQube

```properties
# sonar-project.properties
sonar.coverageReportPaths=coverage/opencover.xml
```

---

## Configuration File (`.cover_me`)

Place a `.cover_me` file in your repository root to configure cover_me. The file uses TOML format.

### Schema Exclusions

```toml
# .cover_me — cover_me configuration for this database repository

[excluded_schemas]
names = [
    "partman",
    "pgtap",
    "public",
]
```

### Behaviour

- **Hardcoded exclusions (always, not configurable):** `pg_*`, `information_schema`, `cover_me`
- **Everything else** — including `public`, `pgtap`, `tap`, and extension schemas — is the repo owner's responsibility to exclude via this file.
- The `-x` CLI flag is **additive** to the config file — schemas from both sources are merged.
- If no `.cover_me` file is found, only the hardcoded exclusions apply.
- Pass `--config /path/to/.cover_me` to use a file from a non-default location.

---

## Troubleshooting

| Problem | Cause | Solution |
| ------- | ----- | -------- |
| 0% coverage (Postgres) | stderr not captured | Add `2> trace.txt` to your test command |
| 0% coverage (MySQL) | Functions not exercised | Verify tests actually call the instrumented functions |
| "No stored procedures/functions found" | Wrong database or no functions | Check `-d` flag points to the correct database |
| Permission denied (MySQL) | Binary logging restrictions | Set `log_bin_trust_function_creators = 1` |
| Functions broken after crash | `untrace` not run | Re-run `cover_me untrace` with the same `-c` cache dir |
| Cache dir missing | Different `-c` path between commands | Use the same `-c` value for trace, report, and untrace |
| Duplicate tag warnings | Same function OID reused | Clean the cache dir and re-run trace |

---

## Project Structure

```
cover_me/
├── pyproject.toml
├── Dockerfile
├── requirements.txt
├── README.md
├── cover_me/
│   ├── __init__.py
│   ├── cli.py                 # CLI entry point
│   ├── instrumenter.py        # Tokeniser + instrumentation logic
│   ├── models.py              # ProcedureDef model, cache I/O
│   ├── profile.py             # Tag profile, trace file parser
│   ├── reporter.py            # OpenCover XML generation
│   ├── html_reporter.py       # HTML report generation
│   ├── pg/
│   │   ├── __init__.py
│   │   ├── dumper.py          # pg_proc query
│   │   └── installer.py       # CREATE OR REPLACE, RAISE WARNING helpers
│   └── mysql/
│       ├── __init__.py        # information_schema query
│       ├── installer.py       # MyISAM trace table, DROP+CREATE
│       └── profile.py         # Trace table reader
└── tests/
    ├── test_instrumenter.py   # Tokeniser + control flow patterns
    ├── test_report.py         # Profile, OpenCover XML
    ├── test_trace.py          # Dumper, cache, installer
    ├── test_mysql.py          # MySQL-specific instrumentation
    └── test_integration.py    # Full cycle (requires live DB)
```

## Testing

```bash
# Unit tests only (no database required)
python -m pytest tests/ --ignore=tests/test_integration.py -v

# All tests (requires Postgres on :5432 and MySQL on :3306)
python -m pytest tests/ -v
```

Integration tests skip automatically if database containers are not running.

## Dependencies

| Package                  | Purpose                    | Install Extra |
| ------------------------ | -------------------------- | ------------- |
| `lxml`                   | XML generation             | (base)        |
| `psycopg2-binary`        | PostgreSQL connection      | `[postgres]`  |
| `mysql-connector-python` | MySQL connection           | `[mysql]`     |
| `pytest`                 | Testing (dev only)         | —             |

## License

See [LICENSE](LICENSE) in the repository root.
