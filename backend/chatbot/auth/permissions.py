"""
Role-based permissions for Django REST Framework.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS


class HasRole(BasePermission):
    """
    Permission class that checks if the user has a specific role.
    Usage in views: permission_classes = [HasRole('admin')]
    """

    def __init__(self, role: str):
        self.required_role = role

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        
        roles = request.user.get('roles', [])
        return self.required_role in roles


class HasAnyRole(BasePermission):
    """
    Permission class that checks if the user has any of the specified roles.
    Usage: permission_classes = [HasAnyRole(['admin', 'bank_agent'])]
    """

    def __init__(self, roles: list[str]):
        self.required_roles = set(roles)

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        
        user_roles = set(request.user.get('roles', []))
        return bool(user_roles & self.required_roles)


class HasAllRoles(BasePermission):
    """
    Permission class that checks if the user has ALL of the specified roles.
    Usage: permission_classes = [HasAllRoles(['admin', 'bank_agent', 'customer])]
    """

    def __init__(self, roles: list[str]):
        self.required_roles = set(roles)

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        user_roles = set(request.user.get('roles', []))
        return self.required_roles.issubset(user_roles)


class IsAuthenticated(BasePermission):
    """Allows access only to authenticated users (has valid JWT)."""

    def has_permission(self, request, view):
        return (
            request.user is not None
            and isinstance(request.user, dict)
            and request.user.get('is_authenticated', False)
        )


# ─── Pre-defined Role Permissions ────────────────────────────────────────────

class IsAdmin(BasePermission):
    """
    Allows access only to users with the explicit 'admin' realm role.
    Prevents privilege confusion with internal Keycloak roles.
    """

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        roles = set(request.user.get('roles', []))
        return 'admin' in roles

class IsBankAgent(BasePermission):
    """
    Allows access to users with 'bank_agent' OR 'admin' realm role.
    Admins inherit all bank_agent capabilities.
    """

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        roles = set(request.user.get('roles', []))
        return bool(roles & {'bank_agent', 'admin'})


class IsModerator(BasePermission):
    """Allows access only to users with 'moderator' role."""

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        return 'moderator' in request.user.get('roles', [])


class IsAdminOrModerator(BasePermission):
    """Allows access to users with 'admin' or 'moderator' role."""

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return False
        roles = set(request.user.get('roles', []))
        return bool(roles & {'admin', 'moderator'})


class IsAdminOrReadOnly(BasePermission):
    """Admins can do anything, others can only read."""

    def has_permission(self, request, view):
        if not request.user or not isinstance(request.user, dict):
            return request.method in SAFE_METHODS
        
        if 'admin' in request.user.get('roles', []):
            return True
        
        return request.method in SAFE_METHODS


class IsOwnerOrAdmin(BasePermission):
    """
    Object-level permission: only owner or admin can modify.
    Requires the object to have a 'user_id' attribute.
    """

    def has_object_permission(self, request, view, obj):
        if not request.user or not isinstance(request.user, dict):
            return False
        
        if 'admin' in request.user.get('roles', []):
            return True
        
        user_id = request.user.get('user_id')
        obj_user_id = getattr(obj, 'user_id', None)
        
        return str(user_id) == str(obj_user_id)