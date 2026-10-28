"""Minimal database readiness probe, without exposing configuration or errors."""
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
