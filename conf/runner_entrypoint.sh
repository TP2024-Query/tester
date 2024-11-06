#!/bin/sh

until PGPASSWORD=$DATABASE_PASSWORD psql -h "$DATABASE_HOST" -U "$DATABASE_USER" -c '\q'; do
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 1
done

echo "$GITHUB_TOKEN" | docker login ghcr.io -u $GITHUB_USER --password-stdin

python3 manage.py collectstatic --no-input
python3 manage.py migrate
python3 manage.py setup
python3 manage.py runner --processes "$PROC_COUNT"
