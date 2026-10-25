from django.contrib import admin
from django.urls import include, path
from learning import views
urlpatterns = [path('admin/', admin.site.urls), path('accounts/register/', views.register, name='register'), path('accounts/', include('django.contrib.auth.urls')), path('', include('learning.urls'))]
