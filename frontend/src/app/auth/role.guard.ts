import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { KeycloakService } from './keycloak.service';

/**
 * Allows access only to users with the bank_agent or admin realm role.
 * Redirects to /unauthorized otherwise.
 */
export const bankAgentGuard: CanActivateFn = () => {
  const keycloak = inject(KeycloakService);
  const router   = inject(Router);

  if (keycloak.isBankAgent) {
    return true;
  }

  return router.createUrlTree(['/unauthorized']);
};

/**
 * Allows access only to users with the admin realm role.
 * Redirects to /unauthorized otherwise.
 */
export const adminGuard: CanActivateFn = () => {
  const keycloak = inject(KeycloakService);
  const router   = inject(Router);

  if (keycloak.isAdmin) {
    return true;
  }

  return router.createUrlTree(['/unauthorized']);
};
