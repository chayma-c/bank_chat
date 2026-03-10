import { Injectable } from '@angular/core';
import Keycloak from 'keycloak-js';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class KeycloakService {
  private static kc: Keycloak = new Keycloak({
    url:      environment.keycloak.url,
    realm:    environment.keycloak.realm,
    clientId: environment.keycloak.clientId,
  });
  private static _authenticated = false;

  /** Call once from main.ts before Angular bootstraps */
  static async init(): Promise<boolean> {
    KeycloakService._authenticated = await KeycloakService.kc.init({
      onLoad:           'login-required',
      checkLoginIframe: false,
      pkceMethod:       'S256',
    });

    if (KeycloakService._authenticated) {
      setInterval(() => {
        KeycloakService.kc.updateToken(70).catch(() => {
          KeycloakService.kc.logout();
        });
      }, 60_000);
    }

    return KeycloakService._authenticated;
  }

  get authenticated(): boolean {
    return KeycloakService._authenticated;
  }

  get token(): string | undefined {
    return KeycloakService.kc.token;
  }

  get userId(): string {
    return KeycloakService.kc.tokenParsed?.['sub'] ?? 'anonymous';
  }

  get username(): string {
    return KeycloakService.kc.tokenParsed?.['preferred_username'] ?? 'unknown';
  }

  get email(): string {
    return KeycloakService.kc.tokenParsed?.['email'] ?? '';
  }

  /** Extracts the first letter of the username for the avatar */
  get userInitial(): string {
    return this.username.charAt(0).toUpperCase();
  }

  logout(): void {
    KeycloakService.kc.logout({ redirectUri: window.location.origin + '/' });
  }
}