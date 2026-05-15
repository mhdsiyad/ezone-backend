from django.db import models

class Season(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Team(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='teams', null=True, blank=True)
    team_name = models.CharField(max_length=150)
    leader_name = models.CharField(max_length=100)
    leader_contact_number = models.CharField(max_length=20)
    instagram_id = models.CharField(max_length=100, blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.team_name} - {self.leader_name}"
