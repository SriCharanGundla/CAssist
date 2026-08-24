import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from app.core.config import Settings


async def _admin_connection(database_url: str):
    url = make_url(database_url)
    return await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database="postgres",
    )


async def _create_database(database_url: str, database_name: str) -> None:
    connection = await _admin_connection(database_url)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def _drop_database(database_url: str, database_name: str) -> None:
    connection = await _admin_connection(database_url)
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
    finally:
        await connection.close()


async def _seed_legacy_exports(database_url: str) -> None:
    url = make_url(database_url)
    connection = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
    )
    try:
        await connection.execute(
            """
            WITH created_user AS (
                INSERT INTO users (external_auth_id, email)
                VALUES ('migration-test', 'migration@example.test')
                RETURNING id
            ), created_workspace AS (
                INSERT INTO workspaces (name, created_by_user_id)
                SELECT 'Migration test', id FROM created_user
                RETURNING id, created_by_user_id
            ), created_document AS (
                INSERT INTO documents (
                    workspace_id, uploaded_by_user_id, original_filename,
                    mime_type, byte_size, status
                )
                SELECT id, created_by_user_id, 'historical.pdf',
                       'application/pdf', 1, 'ready'
                FROM created_workspace
                RETURNING id, uploaded_by_user_id
            ), created_run AS (
                INSERT INTO processing_runs (
                    document_id, requested_by_user_id, provider, model_id,
                    prompt_version, schema_version, preprocessing_version, status
                )
                SELECT id, uploaded_by_user_id, 'openai', 'historical-model',
                       'historical-prompt', 'historical-schema',
                       'historical-preprocessing', 'succeeded'
                FROM created_document
                RETURNING id, requested_by_user_id
            ), created_result AS (
                INSERT INTO extraction_results (
                    processing_run_id, document_type, raw_provider_output, canonical_data
                )
                SELECT id, 'invoice', '{}'::jsonb, '{}'::jsonb FROM created_run
                RETURNING id, (SELECT requested_by_user_id FROM created_run) AS user_id
            )
            INSERT INTO export_events (
                extraction_result_id, exported_by_user_id, format, exporter_version
            )
            SELECT id, user_id, legacy_format::export_format, 'historical-v1'
            FROM created_result
            CROSS JOIN unnest(ARRAY['json', 'csv', 'xlsx']) AS legacy_format
            """
        )
    finally:
        await connection.close()


async def _read_export_formats(database_url: str) -> tuple[list[str], list[str]]:
    url = make_url(database_url)
    connection = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
    )
    try:
        rows = await connection.fetch(
            "SELECT format::text AS format FROM export_events ORDER BY format::text"
        )
        enum_rows = await connection.fetch(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'export_format'
            ORDER BY enumsortorder
            """
        )
        return (
            [row["format"] for row in rows],
            [row["enumlabel"] for row in enum_rows],
        )
    finally:
        await connection.close()


def test_historical_export_formats_survive_upgrade_to_head() -> None:
    source_url = Settings(_env_file=None).database_url
    database_name = f"cassist_migration_{uuid4().hex}"
    database_url = make_url(source_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    try:
        asyncio.run(_create_database(source_url, database_name))
    except (OSError, asyncpg.PostgresConnectionError):
        pytest.skip("Local PostgreSQL is unavailable")
    try:
        config = Config("alembic.ini")
        config.attributes["database_url_override"] = database_url
        command.upgrade(config, "b2d4f6a8c0e1")
        asyncio.run(_seed_legacy_exports(database_url))
        command.upgrade(config, "head")

        stored_formats, enum_values = asyncio.run(_read_export_formats(database_url))
        assert stored_formats == ["csv", "json", "xlsx"]
        assert enum_values == ["json", "csv", "xlsx", "tally_json"]
    finally:
        try:
            asyncio.run(_drop_database(source_url, database_name))
        except (OSError, asyncpg.PostgresConnectionError):
            pass
