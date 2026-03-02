from django.urls import path
from . import views

urlpatterns = [
    # Route principale du chat
    path('chat/', views.ChatView.as_view(), name='chat'),
    
    # Historique des conversations
    path('conversations/', views.ConversationListView.as_view(), name='conversations'),
    path('conversations/<str:session_id>/', views.ConversationDetailView.as_view(), name='conversation-detail'),
    
    # Health check du LLM
    path('health/', views.HealthCheckView.as_view(), name='health'),
]