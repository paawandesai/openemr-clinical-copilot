#!/usr/bin/env bash
#
# Creates the read-only grader account: one user with the VIEWER role on the
# organisation (and so on its one project). Run on the Langfuse host, from
# /opt/langfuse, after the stack is up. Idempotent: an existing user of that
# email gets its password and role reset to what .env says.
#
# Why SQL: headless initialisation creates exactly one user, sign-up is
# disabled, and on self-hosted OSS Langfuse the SCIM and membership APIs are
# Enterprise-only. So this writes the two rows the UI's invite-and-sign-up
# flow would: `users`, with a bcrypt hash made by Langfuse's own bcryptjs
# (cost 12, as its sign-up path does), and `organization_memberships`.
# Same approach, and same caveat, as provision_langfuse_dashboard.py: it is
# pinned to the schema of 4.37.0.
#
# The password travels as an environment variable into the web container,
# never on a command line and never printed. Only its bcrypt hash reaches
# psql (as a -v variable).

set -euo pipefail

compose=(docker compose --project-directory "$(pwd)"
    -f docker-compose.langfuse.yml -f docker-compose.langfuse.prod.yml)

# Read, not sourced: .env is compose's format, where a value may hold spaces.
env_value() { sed -n "s/^$1=//p" .env; }
LANGFUSE_VIEWER_EMAIL="$(env_value LANGFUSE_VIEWER_EMAIL)"
LANGFUSE_VIEWER_PASSWORD="$(env_value LANGFUSE_VIEWER_PASSWORD)"
[[ -n "$LANGFUSE_VIEWER_EMAIL" && -n "$LANGFUSE_VIEWER_PASSWORD" ]] || {
    echo "LANGFUSE_VIEWER_EMAIL / _PASSWORD missing from .env" >&2
    exit 2
}

org_id=clinical-copilot
bcryptjs=/app/node_modules/.pnpm/bcryptjs@2.4.3/node_modules/bcryptjs

# `-e PW` with no value passes it from this environment, off docker's argv.
hash="$(PW="$LANGFUSE_VIEWER_PASSWORD" "${compose[@]}" exec -T -e PW langfuse-web \
    node -e "process.stdout.write(require('$bcryptjs').hashSync(process.env.PW, 12))")"
[[ "$hash" == '$2a$12$'* && ${#hash} -eq 60 ]] || { echo "bcrypt hash failed" >&2; exit 1; }

"${compose[@]}" exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q \
    -v email="$LANGFUSE_VIEWER_EMAIL" -v hash="$hash" -v org="$org_id" <<'SQL'
BEGIN;
INSERT INTO users (id, name, email, email_verified, password, admin)
VALUES ('grader-viewer', 'Grader (read-only)', :'email', now(), :'hash', false)
ON CONFLICT (email) DO UPDATE SET password = EXCLUDED.password, admin = false;
INSERT INTO organization_memberships (id, org_id, user_id, role)
SELECT 'grader-viewer-membership', :'org', id, 'VIEWER' FROM users WHERE email = :'email'
ON CONFLICT (org_id, user_id) DO UPDATE SET role = 'VIEWER';
-- No project-level override: the org role (VIEWER) is the role everywhere.
DELETE FROM project_memberships
WHERE user_id = (SELECT id FROM users WHERE email = :'email');
COMMIT;
SQL

"${compose[@]}" exec -T postgres psql -U postgres -At -v email="$LANGFUSE_VIEWER_EMAIL" <<'SQL'
SELECT 'viewer: ' || u.email || ' role=' || m.role || ' admin=' || u.admin
FROM users u JOIN organization_memberships m ON m.user_id = u.id WHERE u.email = :'email';
SQL
