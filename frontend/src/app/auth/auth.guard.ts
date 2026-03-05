import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { KeycloakService } from './keycloak.service';

export const authGuard: CanActivateFn = () => {
  const keycloak = inject(KeycloakService);

  if (keycloak.authenticated) {
    return true;
  }

  // If somehow the guard fires without auth, Keycloak will redirect
  return false;
};