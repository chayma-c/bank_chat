import { Routes } from '@angular/router';
import { authGuard }      from './auth/auth.guard';
import { bankAgentGuard, adminGuard } from './auth/role.guard';

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'chat',
    pathMatch: 'full',
  },

  // ── Chat — all authenticated users ──────────────────────────────────────
  {
    path: 'chat',
    loadComponent: () =>
      import('./chat/chat.component').then(m => m.ChatComponent),
    canActivate: [authGuard],
  },

  // ── Fraud Settings — bank_agent and admin only ───────────────────────────
  {
    path: 'fraud-settings',
    loadComponent: () =>
      import('./fraud-settings/fraud-settings.component').then(m => m.FraudSettingsComponent),
    canActivate: [authGuard, bankAgentGuard],
  },

  // ── Admin panel — admin only ─────────────────────────────────────────────
  {
    path: 'admin',
    canActivate: [authGuard, adminGuard],
    children: [
      {
        path: 'fraud-rules',
        loadComponent: () =>
          import('./fraud-admin/fraud-admin.component').then(m => m.FraudAdminComponent),
      },
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
        path: 'users',
        loadComponent: () =>
          import('./admin/users/user-management.component').then(m => m.UserManagementComponent),
      },
      {
        path: '',
        redirectTo: 'fraud-rules',
        pathMatch: 'full',
      },
    ],
  },

  // ── Access denied ────────────────────────────────────────────────────────
  {
    path: 'unauthorized',
    loadComponent: () =>
      import('./shared/unauthorized/unauthorized.component').then(m => m.UnauthorizedComponent),
  },

  {
    path: '**',
    redirectTo: 'chat',
  },
];
