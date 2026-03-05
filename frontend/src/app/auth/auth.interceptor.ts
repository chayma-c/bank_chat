import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { KeycloakService } from './keycloak.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const keycloak = inject(KeycloakService);
  const token = keycloak.token;

  console.log('🔑 Interceptor fired, token present:', !!token);

  if (token) {
    return next(req.clone({
      setHeaders: { Authorization: `Bearer ${token}` },
    }));
  }

  return next(req);
};