"""
Serializers for role management.
"""
from rest_framework import serializers


class RoleSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    composite = serializers.BooleanField(read_only=True)
    clientRole = serializers.BooleanField(read_only=True)
    containerId = serializers.CharField(read_only=True)


class RoleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')


class UserRoleAssignSerializer(serializers.Serializer):
    roles = serializers.ListField(
        child=serializers.CharField(),
        min_length=1,
        help_text="List of role names to assign"
    )


class UserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    enabled = serializers.BooleanField(read_only=True)
    roles = serializers.ListField(child=serializers.CharField(), read_only=True)


class UserListSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    enabled = serializers.BooleanField(read_only=True)


class UserDetailSerializer(UserSerializer):
    createdTimestamp = serializers.IntegerField(read_only=True)
    groups = serializers.ListField(child=serializers.CharField(), read_only=True)