from django.contrib import admin
from django.urls import include, path
from learning import views
from learning.health import health

urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    path('accounts/register/', views.register, name='register'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('accounts/', include('allauth.urls')),
    path('', include('learning.urls')),
]
