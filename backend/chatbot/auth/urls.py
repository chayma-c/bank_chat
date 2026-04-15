"""
URL configuration for role management.
"""
from django.urls import path
from .views import (
    RoleListView,
    RoleDetailView,
    RoleUsersView,
    UserListView,
    UserDetailView,
    UserRolesView,
    CurrentUserView,
)

app_name = 'role_management'

urlpatterns = [
    # Current user
    path('me/', CurrentUserView.as_view(), name='current-user'),
    
    # Role management
    path('roles/', RoleListView.as_view(), name='role-list'),
    path('roles/<str:role_name>/', RoleDetailView.as_view(), name='role-detail'),
    path('roles/<str:role_name>/users/', RoleUsersView.as_view(), name='role-users'),
    
    # User management
    path('users/', UserListView.as_view(), name='user-list'),
    path('users/<str:user_id>/', UserDetailView.as_view(), name='user-detail'),
    path('users/<str:user_id>/roles/', UserRolesView.as_view(), name='user-roles'),
]