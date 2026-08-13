# CAssist private deployment

These files prepare the NAS deployment but do not connect to or change the NAS.

## R2 temporary-upload lifecycle

The development and production document buckets expire only `incoming/` temporary objects after
one day. This is a fallback for interrupted requests and post-commit cleanup failures; permanent
`originals/` objects are never covered by an expiry rule.

```bash
pnpm --dir frontend exec wrangler r2 bucket lifecycle add \
  cassist-dev-originals cassist-incoming-expiry incoming/ --expire-days 1 --force
pnpm --dir frontend exec wrangler r2 bucket lifecycle add \
  cassist-documents cassist-incoming-expiry incoming/ --expire-days 1 --force
```

Verify the rules with `wrangler r2 bucket lifecycle list <bucket>` during deployment audits.

## Automated deployment

Pushes to `main` are path-filtered. Frontend changes run tests and deploy only Cloudflare Pages.
Backend changes run tests, publish one immutable AMD64 image to GHCR, join the tailnet using an
ephemeral `tag:ci` node, and invoke the restricted NAS deployment command over SSH. The NAS creates
an encrypted backup, applies forward-only migrations, deploys and health-checks the API, rolls the
API image back on failure, and starts the worker only when `CASSIST_WORKER_ENABLED=true`.

The NAS account `cassist-deploy` has no Docker-group membership. Its authorized key is bound to
`cassist-deploy-dispatch`, which accepts only the two root-owned CAssist deployment commands; sudoers
also permits only those commands. The deployment command accepts only an immutable digest from
`ghcr.io/example-user/cassist-backend`, uses the workflow's short-lived registry token through a
temporary Docker configuration, and never persists that token.

Production GitHub environment secrets:

- `CLOUDFLARE_ACCOUNT_ID` and a Pages-only `CLOUDFLARE_API_TOKEN`.
- `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` for an ephemeral `tag:ci` identity.
- `NAS_SSH_PRIVATE_KEY` and the pinned `NAS_SSH_HOST_KEY`.

The worker stays disabled until an OpenAI production key and credits have both been confirmed. Set
`CASSIST_WORKER_ENABLED=true` in the protected NAS environment file only after that gate.

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
