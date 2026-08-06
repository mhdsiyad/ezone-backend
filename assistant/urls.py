from django.urls import path

from . import views

urlpatterns = [
    path('chat/', views.AssistantChatView.as_view(), name='assistant-chat'),
    path('models/', views.AssistantModelsView.as_view(), name='assistant-models'),
    path('model/', views.AssistantModelSettingView.as_view(), name='assistant-model-setting'),
    path('workspaces/', views.AssistantWorkspacesView.as_view(), name='assistant-workspaces'),
    path('conversations/', views.ConversationListCreateView.as_view(), name='assistant-conversations'),
    path('conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='assistant-conversation-detail'),
    path('conversations/<int:pk>/duplicate/', views.ConversationDuplicateView.as_view(), name='assistant-conversation-duplicate'),
]
