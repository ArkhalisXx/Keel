# Railway deployment

Use a Railway project with PostgreSQL, Redis, a private S3-compatible bucket,
and two services from this GitHub repository: `web` and `worker`.
Railway does not deploy compose.yaml. Local Docker remains unchanged.

## Services

1. Connect the GitHub repository and select the main branch for `web`.
   The repository's railway.json uses Dockerfile, runs migrations before release,
   collects static assets at startup, binds Railway's PORT, and probes /health/.
2. Add Railway PostgreSQL and Redis to the same project/environment.
   Keep their private networking; they do not need public domains for this app.
3. Add a private Railway Bucket (or another private S3-compatible bucket).
   Copy its S3 credentials and endpoint into the variables below.
4. Add another service from the same GitHub repository, called `worker`.
   In its Settings, set its Railway Config File to `/railway-worker.json`.
   It must run Celery, with no public domain and no HTTP healthcheck.
   Deploy web and complete migrations before first starting the worker.
5. Generate a public Railway domain for web only. Keep each service at one
   replica initially. OCR can require substantial RAM; monitor worker memory.

## Variables

Set these on BOTH web and worker. Reference the database and Redis variables
using Railway's variable picker. If your database services use different names,
select their actual names instead of copying the examples literally.

    DJANGO_SECRET_KEY=<one newly generated secret, same on both services>
    DJANGO_DEBUG=0
    DJANGO_TIME_ZONE=Asia/Manila
    DATABASE_URL=${{Postgres.DATABASE_URL}}
    CELERY_BROKER_URL=${{Redis.REDIS_URL}}
    DJANGO_ALLOWED_HOSTS=<your web domain without https://>
    DJANGO_CSRF_TRUSTED_ORIGINS=https://<your web domain>
    DJANGO_TRUST_PROXY=1
    DJANGO_SECURE_COOKIES=1
    DJANGO_SSL_REDIRECT=1
    DJANGO_HSTS_SECONDS=3600
    AWS_STORAGE_BUCKET_NAME=<bucket name>
    AWS_ACCESS_KEY_ID=<bucket access key>
    AWS_SECRET_ACCESS_KEY=<bucket secret key>
    AWS_S3_ENDPOINT_URL=<bucket HTTPS S3 endpoint>
    AWS_S3_REGION_NAME=<bucket region; use auto only if supported>
    AI_PROVIDER=gemini
    GEMINI_API_KEY=<your private provider key>
    AI_MODEL=<a model available to your account>

Use OPENAI_API_KEY and AI_PROVIDER=openai if using OpenAI instead. Keep both
services' document/OCR limits consistent if overriding .env.example defaults.
Do not upload .env to GitHub. Railway variables replace it in production.
Generate a secret locally with:

    python -c "import secrets; print(secrets.token_urlsafe(50))"

To enable Google sign-in, set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on web.
In the Google OAuth client, add this exact authorized redirect URI:

    https://<your web domain>/accounts/google/login/callback/

Add the live origin to authorized JavaScript origins if using that list.
In Google Auth testing mode, add each tester under Audience > Test users.
For general public access, configure the consent screen's production audience
and comply with any verification requirements Google presents.

## Verify before inviting users

- /health/ returns {"status":"ok"}; login/register and HTTPS work.
- Register a test user, then upload a small PDF. The worker must turn it Ready.
- Download it as its owner; another account must not have access.
- Generate a short reviewer, publish, answer a flashcard, and check Progress.
- Test AI replies and Google login using the live callback.
- Redeploy both services and confirm documents and progress remain available.
- Configure database backups, object retention/backups, and billing alerts.
- Run `python manage.py check --deploy` in the deployed web environment and
  review any remaining settings against your domain and proxy configuration.

Existing local users, documents, and progress are NOT copied automatically.
Keep the local database and upload volume intact. Plan a separate migration
before expecting old accounts or study history on the hosted site.
Create an administrator with `python manage.py createsuperuser` in a Railway
web-service shell (not a local shell pointed at the local database).
A full deployment uses metered compute, database, Redis, and object storage;
check Railway's current plan and cost estimates before enabling it.
