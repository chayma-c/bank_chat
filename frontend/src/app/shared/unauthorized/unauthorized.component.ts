import { Component, inject } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { KeycloakService } from '../../auth/keycloak.service';

@Component({
  selector: 'app-unauthorized',
  standalone: true,
  imports: [RouterModule],
  templateUrl: './unauthorized.component.html',
  styleUrl: './unauthorized.component.css',
})
export class UnauthorizedComponent {
  private keycloak = inject(KeycloakService);
  private router   = inject(Router);

  readonly username = this.keycloak.username;
  readonly roles    = this.keycloak.roles;

  goBack(): void {
    this.router.navigate(['/chat']);
  }

  logout(): void {
    this.keycloak.logout();
  }
}
