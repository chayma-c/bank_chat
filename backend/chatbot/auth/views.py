"""
Role management API views.
Proxies requests to Keycloak Admin API.
"""
import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .authentication import KeycloakAuthentication
from .permissions import IsAuthenticated, IsAdmin
from .keycloak_client import get_admin_token
from .serializers import (
    RoleSerializer,
    RoleCreateSerializer,
    UserRoleAssignSerializer,
    UserListSerializer,
    UserDetailSerializer,
)

class KeycloakAdminProxyMixin:
    authentication_classes = [KeycloakAuthentication]
    permission_classes = [IsAuthenticated, IsAdmin]

    def _get_admin_headers(self, request):
        """
        Always use a fresh admin-service token for Keycloak Admin REST API calls.
        Forwarding the user's own JWT would fail with 401 unless the user holds
        the realm-management client roles inside their token.

        Returns a dict on success, or a DRF Response (503) on failure.
        Callers must check: if isinstance(result, Response): return result
        """
        try:
            admin_token = get_admin_token()
        except ValueError as exc:
            return Response(
                {"error": "Could not obtain Keycloak admin token", "details": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }

    def _keycloak_request(self, method, url, headers, **kwargs):
        # If _get_admin_headers() failed it returns a DRF Response (503).
        # Propagate it immediately so callers receive a proper error response
        # instead of crashing with TypeError when requests tries to use it as a dict.
        if isinstance(headers, Response):
            return headers

        try:
            resp = getattr(requests, method)(
                url,
                headers=headers,
                timeout=10,
                **kwargs
            )
            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            try:
                details = e.response.json()
            except Exception:
                details = e.response.text if e.response else None

            return Response(
                {"error": str(e), "details": details},
                status=e.response.status_code if e.response else 500,
            )

        except requests.exceptions.RequestException as e:
            return Response(
                {"error": f"Keycloak request failed: {str(e)}"},
                status=503,
            )
# ─── Role Management ─────────────────────────────────────────────────────────

class RoleListView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/roles/ - List all realm roles
    POST /api/roles/ - Create a new realm role
    """

    def get(self, request):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
        resp = self._keycloak_request('get', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        serializer = RoleSerializer(resp.json(), many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = RoleCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
        resp = self._keycloak_request(
            'post', url, self._get_admin_headers(request), json=serializer.validated_data
        )
        
        if isinstance(resp, Response):
            return resp
        
        return Response(
            {'message': f"Role '{serializer.validated_data['name']}' created successfully"},
            status=status.HTTP_201_CREATED,
        )


class RoleDetailView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/roles/{role_name}/ - Get role details
    PUT /api/roles/{role_name}/ - Update role
    DELETE /api/roles/{role_name}/ - Delete role
    """

    def get(self, request, role_name):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role_name}"
        resp = self._keycloak_request('get', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        serializer = RoleSerializer(resp.json())
        return Response(serializer.data)

    def put(self, request, role_name):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role_name}"
        headers = self._get_admin_headers(request)

        # First, fetch the existing role complete representation
        get_resp = self._keycloak_request('get', url, headers)
        if isinstance(get_resp, Response):
            return get_resp
            
        existing_role = get_resp.json()
        print(f"DEBUG: Existing role: {existing_role}")

        # Merge the incoming data over the existing role representation
        for key, value in request.data.items():
            existing_role[key] = value

        print(f"DEBUG: Payload for PUT: {existing_role}")

        # Use Keycloak's roles-by-id endpoint to bypass the unstable roles/{name} update bug
        role_id = existing_role.get("id")
        by_id_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles-by-id/{role_id}"

        resp = self._keycloak_request(
            'put', by_id_url, headers, json=existing_role
        )

        if isinstance(resp, Response):
            print(f"DEBUG: Error from Keycloak PUT: {resp.data}")
            return resp

        return Response({'message': f"Role '{role_name}' updated successfully"})

    def delete(self, request, role_name):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role_name}"
        resp = self._keycloak_request('delete', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        return Response({'message': f"Role '{role_name}' deleted successfully"})


class RoleUsersView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/roles/{role_name}/users/ - Get users with a specific role
    """

    def get(self, request, role_name):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role_name}/users"
        resp = self._keycloak_request('get', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        serializer = UserListSerializer(resp.json(), many=True)
        return Response(serializer.data)


# ─── User Management ─────────────────────────────────────────────────────────

class UserListView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/users/ - List all users (with optional search)
    """

    def get(self, request):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users"
        
        params = {
            'max': request.query_params.get('max', 50),
            'first': request.query_params.get('first', 0),
        }
        
        search = request.query_params.get('search')
        if search:
            params['search'] = search
        
        resp = self._keycloak_request(
            'get', url, self._get_admin_headers(request), params=params
        )
        
        if isinstance(resp, Response):
            return resp
        
        serializer = UserListSerializer(resp.json(), many=True)
        return Response(serializer.data)


class UserDetailView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/users/{user_id}/ - Get user details with roles
    PUT /api/users/{user_id}/ - Update user
    DELETE /api/users/{user_id}/ - Delete user
    """

    def get(self, request, user_id):
        # Get user info
        user_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}"
        user_resp = self._keycloak_request('get', user_url, self._get_admin_headers(request))
        
        if isinstance(user_resp, Response):
            return user_resp
        
        user_data = user_resp.json()
        
        # Get user roles
        roles_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
        roles_resp = self._keycloak_request('get', roles_url, self._get_admin_headers(request))
        
        if isinstance(roles_resp, Response):
            return roles_resp
        
        user_data['roles'] = [role['name'] for role in roles_resp.json()]
        
        serializer = UserDetailSerializer(user_data)
        return Response(serializer.data)

    def put(self, request, user_id):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}"
        resp = self._keycloak_request(
            'put', url, self._get_admin_headers(request), json=request.data
        )
        
        if isinstance(resp, Response):
            return resp
        
        return Response({'message': 'User updated successfully'})

    def delete(self, request, user_id):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}"
        resp = self._keycloak_request('delete', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        return Response({'message': 'User deleted successfully'})


class UserRolesView(KeycloakAdminProxyMixin, APIView):
    """
    GET /api/users/{user_id}/roles/ - Get user's roles
    POST /api/users/{user_id}/roles/ - Assign roles to user
    DELETE /api/users/{user_id}/roles/ - Remove roles from user
    """

    def get(self, request, user_id):
        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
        resp = self._keycloak_request('get', url, self._get_admin_headers(request))
        
        if isinstance(resp, Response):
            return resp
        
        roles = resp.json()
        return Response([{'name': role['name'], 'id': role['id']} for role in roles])

    def post(self, request, user_id):
        serializer = UserRoleAssignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get available roles to get their IDs
        roles_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
        roles_resp = self._keycloak_request('get', roles_url, self._get_admin_headers(request))
        
        if isinstance(roles_resp, Response):
            return roles_resp
        
        all_roles = {r['name']: r for r in roles_resp.json()}
        
        # Build role objects with IDs
        role_names = serializer.validated_data['roles']
        roles_to_assign = []
        
        for name in role_names:
            if name not in all_roles:
                return Response(
                    {'error': f"Role '{name}' not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            roles_to_assign.append({
                'id': all_roles[name]['id'],
                'name': name,
            })

        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
        resp = self._keycloak_request(
            'post', url, self._get_admin_headers(request), json=roles_to_assign
        )
        
        if isinstance(resp, Response):
            return resp
        
        return Response({'message': f"Roles {role_names} assigned successfully"})

    def delete(self, request, user_id):
        serializer = UserRoleAssignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Same logic as POST - need role IDs
        roles_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
        roles_resp = self._keycloak_request('get', roles_url, self._get_admin_headers(request))
        
        if isinstance(roles_resp, Response):
            return roles_resp
        
        all_roles = {r['name']: r for r in roles_resp.json()}
        
        role_names = serializer.validated_data['roles']
        roles_to_remove = []
        
        for name in role_names:
            if name not in all_roles:
                return Response(
                    {'error': f"Role '{name}' not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            roles_to_remove.append({
                'id': all_roles[name]['id'],
                'name': name,
            })

        url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
        resp = self._keycloak_request(
            'delete', url, self._get_admin_headers(request), json=roles_to_remove
        )
        
        if isinstance(resp, Response):
            return resp
        
        return Response({'message': f"Roles {role_names} removed successfully"})


# ─── Current User Info ───────────────────────────────────────────────────────

class CurrentUserView(APIView):
    """
    GET /api/me/ - Get current authenticated user's info and roles
    """
    authentication_classes = [KeycloakAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'user_id': user.get('user_id'),
            'username': user.get('username'),
            'email': user.get('email'),
            'first_name': user.get('first_name'),
            'last_name': user.get('last_name'),
            'roles': user.get('roles', []),
            'realm_roles': user.get('realm_roles', []),
            'client_roles': user.get('client_roles', []),
            'groups': user.get('groups', []),
        })