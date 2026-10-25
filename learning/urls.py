from django.urls import path
from . import views
urlpatterns = [path('',views.dashboard,name='dashboard'),path('library/',views.library,name='library'),path('documents/<int:pk>/',views.document,name='document'),path('documents/<int:pk>/delete/',views.delete_document,name='delete_document'),path('tutor/',views.tutor,name='tutor'),path('practice/',views.practice,name='practice'),path('practice/<int:pk>/',views.question,name='question'),path('results/<int:pk>/',views.result,name='result')]
