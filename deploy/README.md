# CAssist self-hosted deployment

These files provide a portable Docker Compose deployment for a private Linux host. They contain no
live hostname, SSH identity, storage path, registry owner, or secret.

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
Backend changes run tests, publish one immutable AMD64 image to GHCR, join a private network using an
ephemeral CI node, and invoke the restricted deployment command over SSH. The host creates
an encrypted backup, applies forward-only migrations, deploys and health-checks the API, rolls the
API image back on failure, and starts the worker only when `CASSIST_WORKER_ENABLED=true`.

The deployment account has no Docker-group membership. Its authorized key is bound to
`cassist-deploy-dispatch`, which accepts only the two root-owned CAssist deployment commands; sudoers
also permits only those commands. The deployment command accepts only an immutable digest from the
`CASSIST_IMAGE_REPOSITORY` configured on the host, uses the workflow's short-lived registry token through a
temporary Docker configuration, and never persists that token.

Production GitHub environment secrets:

- `CLOUDFLARE_ACCOUNT_ID` and a Pages-only `CLOUDFLARE_API_TOKEN`.
- `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` for an ephemeral `tag:ci` identity.
- `NAS_SSH_PRIVATE_KEY` and the pinned `NAS_SSH_HOST_KEY` (legacy names retained so existing
  private deployments do not require secret rotation).
- `DEPLOY_SSH_HOST`, kept secret to avoid publishing private network details.

Production GitHub environment variables:

- `BACKEND_IMAGE`, for example `ghcr.io/your-github-user/cassist-backend`.
- `DEPLOY_SSH_USER`.
- `PUBLIC_READY_URL`, for example `https://your-frontend.example/api/v1/ready`.

The worker stays disabled until an OpenAI production key and credits have both been confirmed. Set
`CASSIST_WORKER_ENABLED=true` in the protected host environment file only after that gate.

## PostgreSQL backup

The `backup` Compose service streams a PostgreSQL 17 custom-format dump directly into a
client-side-encrypted Restic repository. Production uses a dedicated private R2 bucket and
bucket-scoped credentials that can read, write, list, and delete objects only in that bucket.
The originals bucket credentials are not reused.

Generate a separate random `RESTIC_PASSWORD`, store it outside both the deployment host and R2, and keep a
tested recovery copy. Losing it makes every backup unrecoverable. The retention policy keeps seven
daily, four weekly, and six monthly snapshots; Restic may also retain the oldest snapshot as a
safety floor. Pruning runs after retention.

Run a backup manually from this directory:

```bash
docker compose --env-file .env.production \
  --file compose.production.yaml --profile ops run --rm backup
```

Install `systemd/cassist-backup.service` and `systemd/cassist-backup.timer` under
`/etc/systemd/system/` only during an authorized rollout. The checked-in unit assumes the
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
