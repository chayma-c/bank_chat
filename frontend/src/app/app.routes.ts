import { Routes } from '@angular/router';
import { authGuard } from './auth/auth.guard'; // your existing guard

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'chat',
    pathMatch: 'full',
  },
  {
    path: 'chat',
    loadComponent: () =>
      import('./chat/chat.component').then(m => m.ChatComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin',
    canActivate: [authGuard],
    children: [
      {
        path: '',
        redirectTo: 'fraud-rules',
        pathMatch: 'full',
      },
      {
        path: 'fraud-rules',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
        data: { activeTab: 'rules' },

      },
      // Placeholder routes for sidebar links — add components as needed
      {
        path: 'decision-logs',
        loadComponent: () =>
          import('./fraud-admin/decision-logs.component').then(m => m.DecisionLogsComponent),
      },
      {
        path: 'model-config',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
        data: { activeTab: 'configs' },
      },
      {
        path: 'fraud-settings',
        loadComponent: () =>
          import('./fraud-settings/fraud-settings.component').then(m => m.FraudSettingsComponent),
        canActivate: [authGuard],
      },
      {
        path: '',
        redirectTo: 'fraud-rules',
        pathMatch: 'full',
      },
    ],
  },
  {
    path: '**',
    redirectTo: 'chat',
  },
];
