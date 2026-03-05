import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';
import { KeycloakService } from './app/auth/keycloak.service';

console.log('🔵 Starting Keycloak init...');

KeycloakService.init()
  .then((authenticated) => {
    console.log('🟢 Keycloak init done, authenticated:', authenticated);
    if (authenticated) {
      bootstrapApplication(App, appConfig)
        .then(() => console.log('🟢 Angular bootstrapped'))
        .catch((err) => console.error('🔴 Angular bootstrap failed:', err));
    } else {
      document.body.innerText = 'Authentication failed. Please try again.';
    }
  })
  .catch((err) => {
    console.error('🔴 Keycloak init failed:', err);
    document.body.innerText = 'Authentication service unavailable. Please try again later.';
  });