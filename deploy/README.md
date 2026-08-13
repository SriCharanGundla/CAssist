# CAssist private deployment

These files prepare the NAS deployment but do not connect to or change the NAS.

## PostgreSQL backup

The `backup` Compose service streams a PostgreSQL 17 custom-format dump directly into a
client-side-encrypted Restic repository. Production uses a dedicated private R2 bucket and
bucket-scoped credentials that can read, write, list, and delete objects only in that bucket.
The originals bucket credentials are not reused.

Generate a separate random `RESTIC_PASSWORD`, store it outside both the NAS and R2, and keep a
tested recovery copy. Losing it makes every backup unrecoverable. The retention policy keeps seven
daily, four weekly, and six monthly snapshots; Restic may also retain the oldest snapshot as a
safety floor. Pruning runs after retention.

Run a backup manually from this directory:

```bash
docker compose --env-file .env.production \
  --file compose.production.yaml --profile ops run --rm backup
```

Install `systemd/cassist-backup.service` and `systemd/cassist-backup.timer` under
`/etc/systemd/system/` only during the authorized NAS rollout. The checked-in unit assumes the
repository is located at `/opt/cassist`; it runs nightly at 02:15 with up to 15 minutes of jitter
and catches up after downtime.

## Restore test

Run the self-contained local test from the repository root:

```bash
./deploy/backup/test-local-restore
```

It creates isolated temporary Docker resources, makes three encrypted backups, proves the wrong
password cannot open them, exercises retention and pruning, checks repository integrity, restores
the latest dump into a fresh PostgreSQL container, verifies the restored data, and cleans up.

For an operator-initiated production restore, stop API and worker writes, restore into a new empty
PostgreSQL instance, validate it, and only then promote that instance. Do not overwrite the sole
production database in place. The underlying restore command is:

```bash
docker compose --env-file .env.production --file compose.production.yaml \
  --profile ops run --rm --entrypoint restore-postgres backup latest
```
