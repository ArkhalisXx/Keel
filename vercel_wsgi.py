"""Explicit Vercel WSGI entrypoint; never starts a worker or writes local data."""
import os

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_vercel'

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
