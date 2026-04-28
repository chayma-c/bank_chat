import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { KeycloakAdminService, KeycloakUser } from '../../services/keycloak-admin.service';

@Component({
  selector: 'app-user-management',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './user-management.component.html',
  styleUrls: ['./user-management.component.css']
})
export class UserManagementComponent implements OnInit {
  private readonly adminService = inject(KeycloakAdminService);
  
  users = signal<KeycloakUser[]>([]);
  isLoading = signal<boolean>(true);
  error = signal<string | null>(null);

  ngOnInit(): void {
    this.loadUsers();
  }

  loadUsers(): void {
    this.isLoading.set(true);
    this.adminService.getUsers().subscribe({
      next: (users) => {
        this.users.set(users);
        this.isLoading.set(false);
      },
      error: (err) => {
        this.error.set('Failed to load users. Are you an admin?');
        this.isLoading.set(false);
        console.error(err);
      }
    });
  }

  toggleRole(user: KeycloakUser, role: 'bank_agent' | 'admin'): void {
    const hasRole = user.roles.includes(role);
    const action = hasRole ? 'remove' : 'add';
    
    this.adminService.updateUserRole({ user_id: user.id, role, action }).subscribe({
      next: () => {
        this.loadUsers(); // Refresh
      },
      error: (err) => {
        alert(`Error: ${err.error?.error || 'Unknown error'}`);
      }
    });
  }
}
