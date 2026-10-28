from django.urls import path
from . import views, assistant_views, exam_views
urlpatterns = [
    path('papers/<int:pk>/publish/',exam_views.publish_paper,name='publish_paper'),
    path('papers/<int:pk>/generation/',exam_views.reviewer_status,name='reviewer_status'),
    path('papers/<int:pk>/retry-generation/',exam_views.retry_reviewer,name='retry_reviewer'),

    path('assistant/', assistant_views.assistant_page, name='assistant'),
    path('assistant/chat/', assistant_views.assistant_chat, name='assistant_chat'),
    path('papers/import/', exam_views.import_paper, name='import_paper'),
    path('papers/<int:pk>/', exam_views.paper_detail, name='paper_detail'),
    path('papers/questions/<int:pk>/review/', exam_views.review_question, name='review_question'),
path('documents/<int:pk>/status/',views.document_status,name='document_status'),path('documents/<int:pk>/retry/',views.retry_document,name='retry_document'),path('',views.dashboard,name='dashboard'),path('library/',views.library,name='library'),path('documents/<int:pk>/',views.document,name='document'),path('documents/<int:pk>/delete/',views.delete_document,name='delete_document'),path('tutor/',views.tutor,name='tutor'),path('practice/',views.practice,name='practice'),path('practice/<int:pk>/',views.question,name='question'),path('results/<int:pk>/',views.result,name='result')]
