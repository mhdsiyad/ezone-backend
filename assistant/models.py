from django.conf import settings
from django.db import models

WORKSPACE_CHOICES = [
    ('general', 'General'),
    ('fixture', 'Fixtures'),
    ('auction', 'Auction'),
    ('teams', 'Teams'),
    ('players', 'Players'),
    ('tournament', 'Tournament'),
    ('results', 'Results'),
]


class AIRuntimeSetting(models.Model):
    """Singleton row holding the currently active provider/model. Read on
    every chat request and updated by POST /api/v1/assistant/model/ — this is
    what makes a runtime model switch survive a server restart without any
    Redis/cache dependency (this project runs on plain SQLite)."""
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        setting, _ = cls.objects.get_or_create(
            pk=1,
            defaults={'provider': settings.AI_PROVIDER, 'model': settings.AI_MODEL},
        )
        return setting

    def __str__(self):
        return f'{self.provider}/{self.model}'


class Conversation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_conversations')
    title = models.CharField(max_length=200, default='New chat')
    workspace = models.CharField(max_length=20, choices=WORKSPACE_CHOICES, default='general')
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)
    is_archived = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    is_favorite = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_pinned', '-updated_at']

    def __str__(self):
        return self.title


class Message(models.Model):
    ROLE_CHOICES = [('user', 'User'), ('assistant', 'Assistant'), ('system', 'System')]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField(blank=True, default='')
    tool_calls = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.role}: {self.content[:40]}'
