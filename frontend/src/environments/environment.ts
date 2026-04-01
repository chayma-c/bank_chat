/*export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:8000/api/v1/chatbot',
  keycloak: {
    url:      'http://localhost:8080',
    realm:    'myrealm',
    clientId: 'bank_chat',
  },
};*/

export const environment = {
  production: false,
 
  // ── Une seule URL pour tout ──────────────────────────────────────────────
  apiBaseUrl: 'http://localhost/api/v1/chatbot',   // → gateway → Django :8000
  fraudUrl:   'http://localhost/api/fraud',         // → gateway → fraud-service :8001
 
  // ── Keycloak ─────────────────────────────────────────────────────────────
  keycloak: {
    url:      'http://localhost/auth',              // → gateway → keycloak :8080
    realm:    'myrealm',
    clientId: 'bank_chat',
  },
};