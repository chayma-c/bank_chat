import { Routes } from '@angular/router';
import { ChatComponent } from './chat/chat.component';
import { FraudSettingsComponent } from './fraud-settings/fraud-settings.component';

export const routes: Routes = [
  { path: '', component: ChatComponent },
  { path: 'fraud-settings', component: FraudSettingsComponent },
  { path: '**', redirectTo: '' },
];
