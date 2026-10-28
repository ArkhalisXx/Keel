# Render Free with Supabase database and private storage

This is a constrained small-site configuration, not a guarantee of free usage
or always-on processing. Confirm each provider's current free plan in its own
dashboard before creating resources. Do not enable paid plans or paid add-ons.
The official pricing pages could not be reached from the preparation workspace.

## Architecture and limits

- Render: one Free Docker web service using render.yaml. No paid worker or Redis.
- Supabase Free: PostgreSQL and a PRIVATE Storage bucket accessed over S3.
- One database queue scheduler runs in the web container, only while it is awake.
  Jobs run one at a time in timeout-bounded subprocesses. Saved queue rows and
  completed reviewer batches survive restarts. Interrupted jobs require Retry.
- Free hosting can sleep, restart, throttle, or suspend at quota limits. Expect
  cold starts. Do not use uptime pings to bypass a provider's free-tier rules.
- This profile allows uploads up to 10 MB, PDFs up to 150 pages, and extracted
  text up to 1 million characters. OCR is OFF; scanned-only PDFs must be OCR'd
  locally before upload. Split large papers. Reviewer requests still use the
  configured AI provider's quota and may incur its separate API charges.
- Keep ONE web replica. Do not add a Celery worker to this database-queue setup.
- Files must use external object storage. Render's local filesystem is temporary.
- Local Docker and the existing Celery setup keep their original defaults.

## 1. Create the database and storage in Supabase

1. Open https://supabase.com/dashboard and create a project on the Free plan.
   Save its database password privately. Choose a region near your Render region.
2. Under Storage, create a bucket named `keel-documents`. Keep Public OFF.
3. Find Storage's S3 connection settings. Generate server-side S3 access credentials
   and copy the endpoint, region, access key ID, and secret key. Do not use an
   anon/publishable key or a service-role JWT in place of S3 access credentials.
4. In the project's Connect dialog, copy the PostgreSQL Session Pooler connection
   URI (IPv4 compatible). Replace its password placeholder, percent-encoding any
   special characters in the password, and require TLS using `?sslmode=require`
   (or `&sslmode=require` if the URI already contains query parameters).
   Prefer the session pooler here, not the transaction pooler or an IPv6-only
   direct endpoint. Use the host, username, and database from the dialog exactly.
5. Review the free plan's database, object storage, egress, project inactivity,
   and backup limits. Set up your own exports/backups before relying on the site.

## 2. Create the Render web service

1. Open https://dashboard.render.com and connect GitHub to ArkhalisXx/Keel.
2. Choose New > Blueprint and select the repository, branch main. The repository
   includes render.yaml. Confirm it creates ONLY one FREE web service, no database
   or paid worker. If that plan isn't available, stop instead of choosing paid.
3. Supply the requested external connection/storage variables:

       DATABASE_URL=<Supabase Session Pooler URI with sslmode=require>
       AWS_STORAGE_BUCKET_NAME=keel-documents
       AWS_ACCESS_KEY_ID=<server-side S3 access key ID>
       AWS_SECRET_ACCESS_KEY=<server-side S3 secret key>
       AWS_S3_ENDPOINT_URL=<exact S3 HTTPS endpoint from Supabase>
       AWS_S3_REGION_NAME=<exact region from Supabase>

   The blueprint generates DJANGO_SECRET_KEY and configures secure cookies,
   HTTPS proxy handling, lower resource limits, and JOB_BACKEND=database.
   Do not paste secrets into screenshots, source files, or chat messages.
4. For manual service creation instead of a Blueprint, choose Docker, Free,
   Docker Command `python scripts/start_render.py`, and Health Check `/health/`.
   Copy ALL environment variables from render.yaml, generating your own secret.
   A manual service must not use the Dockerfile's default Gunicorn-only command.
5. Deploy. Startup runs migrations, collects static files, and starts Gunicorn
   and the queue scheduler. Check service logs for successful startup.

## 3. Configure the domain and optional AI/Google

After Render assigns an HTTPS address such as https://keel-example.onrender.com,
add these environment variables and redeploy:

    DJANGO_ALLOWED_HOSTS=keel-example.onrender.com
    DJANGO_CSRF_TRUSTED_ORIGINS=https://keel-example.onrender.com
    AI_PROVIDER=gemini
    GEMINI_API_KEY=<your own private API key>
    AI_MODEL=<model supported by your account>

Do not include https:// in DJANGO_ALLOWED_HOSTS. The default Render hostname is
also automatically accepted; custom domains must be listed explicitly. For
OpenAI use AI_PROVIDER=openai and OPENAI_API_KEY instead. AI provider billing
and free limits are independent of Render/Supabase hosting.

Google login is optional. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to Render.
In the Google OAuth client, add the exact authorized redirect URI:

    https://keel-example.onrender.com/accounts/google/login/callback/

Keep the trailing slash. Configure the consent screen audience/test users.

## 4. Verify before inviting users

- Visit /health/ and confirm status ok; create a fresh standard account.
- Upload a small text-based PDF/TXT. Keep the site open and confirm it becomes Ready.
- Generate a five-question reviewer, publish it, answer cards, and check Progress.
- Sign in with another account and verify it cannot access the first user's files.
- Redeploy and verify the original file and study progress are still available.
- Test Google login and AI if configured. Check provider quotas separately.
- Test job Retry after interruptions; reviewer retries preserve saved questions.
- Monitor RAM, compute hours, storage size, database size, and outbound transfer.

A newly hosted installation starts with an empty database. Existing local users,
documents, and analytics are NOT transferred. Preserve local Docker volumes.
No administrator is created automatically. Free Render services may not offer a
shell; ordinary registration does not require admin access. Arrange a secure
one-time management command against the hosted database separately if needed.

Do not use Render's time-limited free PostgreSQL database for permanent study
history without checking its current expiry terms. This setup deliberately uses
external PostgreSQL and private object storage instead of local SQLite/uploads.
