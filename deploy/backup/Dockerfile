FROM postgres:17-alpine

RUN apk add --no-cache restic

COPY --chmod=0555 backup-postgres /usr/local/bin/backup-postgres
COPY --chmod=0555 restore-postgres /usr/local/bin/restore-postgres

USER postgres

ENTRYPOINT ["backup-postgres"]
