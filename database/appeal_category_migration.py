"""Add category snapshots without updating any existing decision or discipline data."""


def migrate_appeal_categories(conn):
    for table in ('appeals', 'decision_history'):
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        for name, ddl in (
            ('decision_category_code', 'TEXT'),
            ('decision_category_label', 'TEXT'),
            ('decision_category_version', 'INTEGER'),
        ):
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
