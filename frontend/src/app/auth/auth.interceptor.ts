/**
 * auth.interceptor.ts
 *
 * Attaches the Keycloak JWT Bearer token to every outgoing HTTP request
 * that targets the API backend or the fraud-service.
 *
 * Without this, HttpClient calls (like DecisionLogsService.getLogs())
 * reach the fraud-service without an Authorization header → 401/503.
 *
 * The fetch()-based streaming in chat.service.ts already handles
 * its own token manually — this interceptor covers HttpClient only.
 *
 * Place at: src/app/auth/auth.interceptor.ts
 */

import { HttpInterceptorFn, HttpRequest, HttpHandlerFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { KeycloakService } from './keycloak.service';
import { environment } from '../../environments/environment';

/** URL prefixes that require a Bearer token */
const PROTECTED_ORIGINS = [
  environment.apiBaseUrl,   // Django orchestrator  e.g. http://localhost/api/v1/chatbot
  environment.fraudUrl,     // Fraud service         e.g. http://localhost/fraud
];

export const authInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
) => {
  const keycloak = inject(KeycloakService);
  const token    = keycloak.token;

  const needsAuth = token && PROTECTED_ORIGINS.some(origin => req.url.startsWith(origin));

  if (!needsAuth) {
    return next(req);
  }

  const authReq = req.clone({
    setHeaders: { Authorization: `Bearer ${token}` },
  });

  return next(authReq);
};