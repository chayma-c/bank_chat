import { Injectable } from '@angular/core';
import Keycloak from 'keycloak-js';
import { environment } from '../../environments/environment';
//singleton
@Injectable({ providedIn: 'root' })
export class KeycloakService {
  private static kc: Keycloak = new Keycloak({
    url: environment.keycloak.url,
    realm: environment.keycloak.realm,
    clientId: environment.keycloak.clientId,
  });
  private static _authenticated = false;

  /** Call once from main.ts before Angular bootstraps */
  static async init(): Promise<boolean> {
    KeycloakService._authenticated = await KeycloakService.kc.init({
      onLoad: 'login-required',
      checkLoginIframe: false,
      pkceMethod: 'S256',
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
    return KeycloakService.kc.tokenParsed?.['preferred_username']
      ?? KeycloakService.kc.tokenParsed?.['name']
      ?? KeycloakService.kc.subject
      ?? 'anonymous';
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

  get isAdmin(): boolean {
    // Check if the user has the 'admin' role either globally (realm role)
    // or specifically for this application client (resource/client role).
    const isAdm = KeycloakService.kc.hasRealmRole('admin') || KeycloakService.kc.hasResourceRole('admin');
    console.log('DEBUG: Kc parsed token:', KeycloakService.kc.tokenParsed);
    console.log('DEBUG: Evaluated isAdmin:', isAdm);
    return isAdm;
  }

  logout(): void {
    KeycloakService.kc.logout({ redirectUri: window.location.origin + '/' });
  }
}