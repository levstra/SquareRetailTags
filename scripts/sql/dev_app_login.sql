-- Local dev only: an app login that is subject to row-level security.
-- (The docker `esl` user is a superuser and silently bypasses RLS.)
-- Production logins are created by Terraform (rollout step 5).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_app_dev') THEN
    CREATE ROLE esl_app_dev LOGIN PASSWORD 'esl_app_dev';
  END IF;
END $$;
GRANT esl_app TO esl_app_dev;
