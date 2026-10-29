# Deploy Keel from the deploy/vercel branch

This branch prepares Django for Vercel's Python/Django runtime. It is separate
from main; local Docker still defaults to its existing PostgreSQL/Redis/Celery
setup. Do not merge this branch into main just to deploy it.

## What changes on Vercel

- External PostgreSQL stores users, questions, and progress. Never use SQLite.
- A private S3-compatible bucket stores uploaded documents. Never use local uploads.
- There is no always-running worker or Redis. While Library is open, the browser
  submits a CSRF-protected request to process one owned queued document. While
  a reviewer is open, it requests one five-question batch at a time. Keep those
  pages open until finished. Status GETs do not perform work.
- If a request's outcome is unknown, the page checks status and asks you to
  Continue instead of silently repeating an AI request. A terminated function
  may show Processing until its deadline expires; use Retry after that.
- Uploads are capped at 3 MB, 100 PDF pages, and 500,000 extracted characters.
  OCR is OFF because this runtime does not provide Tesseract. OCR scans on your
  local computer first, or use a text-based PDF/DOCX/TXT. Split large papers.
- Functions are configured for 60 seconds. Slow providers/documents can still
  time out. This profile is for modest use; it does not promise Docker-scale
  processing, availability, or unlimited free usage.
- Private downloads redirect to an owner-authorized signed storage URL valid
  for 60 seconds, avoiding large file responses through the function.

The Python builder uses pyproject.toml and uv.lock, excluding OCR/native PDF
rendering dependencies. Vercel serves collected static files from its CDN.
The deployment does not use Dockerfile or compose.yaml.

## 1. Prepare persistent data services

Supabase can supply both PostgreSQL and private S3-compatible Storage. Choose
its Free plan only if its current quotas and project-inactivity rules fit your
usage. Alternatively use another TLS PostgreSQL host and private S3 provider.
Free allowances can change; review the providers' current plans yourself.

1. Create the database project and save its password privately.
2. Copy a pooler PostgreSQL connection URI from Connect. For Supabase, the
   Session Pooler is IPv4-compatible. Use the exact host and username shown.
   Replace the password placeholder and percent-encode special characters.
   Include sslmode=require in its query parameters. Transaction poolers are
   supported by disabling server-side cursors and prepared statements, but use
   the provider's documented serverless connection settings and connection caps.
3. Create a bucket named keel-documents with Public OFF.
4. Generate S3 credentials in Storage's S3 settings. Copy the endpoint, region,
   access key ID, and secret key. These are server-side S3 credentials, not an
   anon key or browser-facing publishable key. Do not put them in JavaScript.

## 2. Set up the Vercel project

1. Sign in at https://vercel.com/ and choose Add New > Project.
2. Connect GitHub and import ArkhalisXx/Keel.
3. Set Framework Preset to Django and Root Directory to the repository root.
4. Configure the project to deploy the deploy/vercel branch. In the project,
   Settings > Git > Production Branch must be deploy/vercel, NOT main. If the
   import screen initially selects main, change the branch setting before the
   deployment you intend to use, then create a deployment from deploy/vercel.
5. Leave install/output overrides at their framework defaults. vercel.json sets
   the build check and function duration; pyproject.toml names the WSGI entrypoint.
6. Select a plan whose terms cover your usage. Confirm Hobby/free eligibility
   and current function, bandwidth, and storage provider quotas. This repository
   does not create paid resources or guarantee a zero bill for external APIs.

## 3. Configure environment variables

Add these in the Vercel project's Settings > Environment Variables, scoped to
Production. Use different database/storage credentials if enabling previews;
preview jobs and migrations must not mutate production study data.

    DJANGO_SETTINGS_MODULE=config.settings_vercel
    DJANGO_SECRET_KEY=<a new private secret>
    DATABASE_URL=postgresql://user:password@host/database?sslmode=require
    AWS_STORAGE_BUCKET_NAME=keel-documents
    AWS_ACCESS_KEY_ID=<S3 access key ID>
    AWS_SECRET_ACCESS_KEY=<S3 secret key>
    AWS_S3_ENDPOINT_URL=<exact HTTPS endpoint from storage provider>
    AWS_S3_REGION_NAME=<exact storage region>
    DJANGO_ALLOWED_HOSTS=<your assigned production host, without https://>
    DJANGO_CSRF_TRUSTED_ORIGINS=https://<your assigned production host>
    DJANGO_TIME_ZONE=Asia/Manila

Generate a secret locally using Docker if Python is not installed:

    docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(50))"

Vercel's VERCEL_URL and VERCEL_PROJECT_PRODUCTION_URL are accepted automatically
as exact hosts. Add any custom domain explicitly. The serverless profile forces
DEBUG off, secure cookies, HTTPS, private storage, and the smaller limits.
Never upload .env; .vercelignore excludes it even for CLI deployments.

For AI, add AI_PROVIDER=gemini, GEMINI_API_KEY, and AI_MODEL using a model your
account supports. Or choose AI_PROVIDER=openai with OPENAI_API_KEY and a model
supported by that account. Provider API quota/billing is separate from hosting.

For optional Google login, add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. In
Google Cloud's OAuth client, add the exact authorized redirect URI:

    https://<your production host>/accounts/google/login/callback/

Keep the trailing slash. Configure the consent screen's test users/audience.

## 4. Create database tables before using the site

Migrations are deliberately NOT run on function startup or during every Vercel
build. Run them once against the intended hosted database, then whenever a
later release adds migrations. This avoids concurrent cold starts and previews
changing a production database.

To keep your existing local installation separate, clone this branch into a new
folder using Windows CMD:

    git clone --branch deploy/vercel https://github.com/ArkhalisXx/Keel.git Keel-Vercel
    cd Keel-Vercel

In that folder, create a private .env.vercel file containing
the required secret/database/storage variables above. This file is ignored by
Git and excluded from the Docker build context. Keep it private.

With Docker Desktop running, in Windows CMD in that branch's project folder:

    docker build -t keel-vercel-admin .
    docker run --rm --env-file .env.vercel -e DJANGO_SETTINGS_MODULE=config.settings_vercel keel-vercel-admin python manage.py migrate --noinput

Optional administrator:

    docker run --rm -it --env-file .env.vercel -e DJANGO_SETTINGS_MODULE=config.settings_vercel keel-vercel-admin python manage.py createsuperuser

These commands connect to the database in .env.vercel, not local Docker Compose.
Double-check the target before running them. A new hosted database starts empty;
local users, uploaded files, and study progress are not copied automatically.

## 5. Deploy and verify

1. Deploy from deploy/vercel after configuring environment variables. Redeploy
   after later variable changes; they do not update an already built deployment.
2. Check /health/ returns status ok. This checks database connectivity only.
3. Verify the login page, CSS, images, and registration over HTTPS.
4. Register a test user, upload a small text-based document, and keep Library
   open until Ready. No separate worker should be required.
5. Open the document link and confirm the private download works.
6. Generate a five-question reviewer; keep its page open until complete. Publish,
   answer flashcards, and verify Progress.
7. Sign in with another account and check files/jobs cannot be accessed across
   accounts. Test Google OAuth and AI if configured.
8. Redeploy and verify files and study history are still present.
9. Configure database/object backups and monitor free-tier and AI quotas.

A provider-specific live deployment still needs your account access, actual
credentials, and these end-to-end checks. Local mocked tests cannot verify
Vercel routing, your S3 provider's permissions, or OAuth on the live domain.

The deploy check may warn that HSTS subdomains/preload are not enabled. Those
policies are deliberately not forced for domains this project does not control.
