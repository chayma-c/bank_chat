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
        path: 'fraud-rules',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
      },
      // Placeholder routes for sidebar links — add components as needed
      {
        path: 'decision-logs',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
      },
      {
        path: 'model-config',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
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
