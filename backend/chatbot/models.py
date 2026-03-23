from django.db import models
import uuid

class Conversation(models.Model):
    session_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user_id    = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    summary          = models.TextField(blank=True, default='')
    archived_count   = models.IntegerField(default=0)
    last_archived_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['-updated_at']

class Message(models.Model):
    ROLE_CHOICES = [
        ('user',      'User'),
        ('assistant', 'Assistant'),
        ('system',    'System'),
    ]
    conversation = models.ForeignKey(Conversation, related_name='messages', on_delete=models.CASCADE)
    role         = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content      = models.TextField()
    agent_used   = models.CharField(max_length=100, blank=True, null=True)
    tokens_used  = models.IntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at']