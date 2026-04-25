import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface KeycloakUser {
  id: string;
  username: string;
  email?: string;
  firstName?: string;
  lastName?: string;
  enabled: boolean;
  roles: string[];
}

export interface RoleUpdatePayload {
  user_id: string;
  role: 'bank_agent' | 'admin';
  action: 'add' | 'remove';
}

@Injectable({ providedIn: 'root' })
export class KeycloakAdminService {
  private readonly base = environment.apiBaseUrl;
  private readonly http = inject(HttpClient);

  getUsers(): Observable<KeycloakUser[]> {
    return this.http.get<KeycloakUser[]>(`${this.base}/admin/users/list/`);
  }

  updateUserRole(payload: RoleUpdatePayload): Observable<any> {
    return this.http.post(`${this.base}/admin/users/update-role/`, payload);
  }
}
