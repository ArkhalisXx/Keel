"""Small readiness response without exposing database or storage credentials."""
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        response = JsonResponse({'status': 'ok'})
    except Exception:
        response = JsonResponse({'status': 'unavailable'}, status=503)
    response['Cache-Control'] = 'no-store'
    return response
