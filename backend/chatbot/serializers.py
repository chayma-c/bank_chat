from rest_framework import serializers
from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Message
        fields = ['id', 'role', 'content', 'agent_used', 'tokens_used', 'created_at']


class ConversationSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model  = Conversation
        fields = ['session_id', 'user_id', 'created_at', 'updated_at', 'message_count', 'messages']

    def get_message_count(self, obj):
        return obj.messages.count()
