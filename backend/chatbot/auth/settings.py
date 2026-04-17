# Keycloak settings
KEYCLOAK_URL = env('KEYCLOAK_URL', default='http://localhost:8080')
KEYCLOAK_REALM = env('KEYCLOAK_REALM', default='my-realm')
KEYCLOAK_CLIENT_ID = env('KEYCLOAK_CLIENT_ID', default='bank_chat')

# REST Framework settings
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "chatbot.auth.authentication.KeycloakAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}