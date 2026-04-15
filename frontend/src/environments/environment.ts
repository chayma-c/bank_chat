export const environment = {
  production: false,
 
  // ── Une seule URL pour tout ──────────────────────────────────────────────
  apiBaseUrl: 'http://localhost/api/v1/chatbot',   // → gateway → Django :8000
  fraudUrl:   'http://localhost/fraud',         // → gateway → fraud-service :8001
 
  // ── Keycloak ─────────────────────────────────────────────────────────────
  keycloak: {
    url:      'http://localhost/auth',              // → gateway → keycloak :8080
    realm:    'myrealm',
    clientId: 'bank_chat',
  },
};