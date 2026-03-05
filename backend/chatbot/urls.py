from django.urls import path
from . import views

urlpatterns = [
    # Chat — standard (blocking) and streaming
    path('chat/',        views.ChatView.as_view(),       name='chat'),

    # Conversation history
    path('conversations/',                       views.ConversationListView.as_view(),   name='conversations'),
    path('conversations/<str:session_id>/',      views.ConversationDetailView.as_view(), name='conversation-detail'),

    # Health check
    path('health/', views.HealthCheckView.as_view(), name='health'),
]