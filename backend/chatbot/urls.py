from django.urls import path, include
from . import views

urlpatterns = [
    # Chat — standard (blocking) and streaming
    path('chat/',        views.ChatView.as_view(),       name='chat'),
    path('chat/stream/', views.StreamChatView.as_view(), name='chat-stream'),

    # ✨ Fraud Detection Agent — direct API endpoint
    path('fraud/analyze/', views.FraudAnalyzeView.as_view(), name='fraud-analyze'),

    # Conversation history
    path('conversations/',                       views.ConversationListView.as_view(),   name='conversations'),
    path('conversations/<str:session_id>/',      views.ConversationDetailView.as_view(), name='conversation-detail'),

    # Health check
    path('health/', views.HealthCheckView.as_view(), name='health'),

    # Admin / User Management
    path('admin/users/list/',        views.UserListView.as_view(),       name='admin-user-list'),
    path('admin/users/update-role/', views.UserRoleUpdateView.as_view(), name='admin-user-role-update'),

    # Auth / role management
    path('', include('chatbot.auth.urls')),
]