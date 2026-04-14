import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import { FraudService, FraudSettings } from '../services/fraud.service';

@Component({
  selector: 'app-fraud-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './fraud-settings.component.html',
  styleUrl: './fraud-settings.component.css'
})
export class FraudSettingsComponent implements OnInit {
  private fraudService = inject(FraudService);
  private router = inject(Router);

  settings = signal<FraudSettings>({
    frequency: 'manual',
    time: '00:00',
    dayOfWeek: 1
  });

  isSaving = signal(false);
  isTriggering = signal(false);
  showSuccess = signal(false);
  
  // Toast Notification
  notification = signal<{show: boolean, message: string, type: 'success' | 'error' | 'info'}>({
    show: false,
    message: '',
    type: 'info'
  });

  daysOfWeek = [
    { value: 1, label: 'Lundi' },
    { value: 2, label: 'Mardi' },
    { value: 3, label: 'Mercredi' },
    { value: 4, label: 'Jeudi' },
    { value: 5, label: 'Vendredi' },
    { value: 6, label: 'Samedi' },
    { value: 0, label: 'Dimanche' }
  ];

  ngOnInit() {
    this.fraudService.getSettings().subscribe({
      next: (data) => {
        this.settings.set({
          ...data,
          time: data.time || '00:00',
          dayOfWeek: data.dayOfWeek ?? 1,
          lastStatus: data.lastStatus || 'ready'
        });
      },
      error: (err) => {
        console.error('Failed to load settings:', err);
        this.showToast('Erreur lors du chargement des paramètres', 'error');
      }
    });
  }

  showToast(message: string, type: 'success' | 'error' | 'info' = 'success') {
    this.notification.set({ show: true, message, type });
    setTimeout(() => {
      this.notification.set({ ...this.notification(), show: false });
    }, 4000);
  }

  saveSettings() {
    this.isSaving.set(true);
    this.fraudService.updateSettings(this.settings()).subscribe({
      next: () => {
        this.isSaving.set(false);
        this.showToast('Planification mise à jour avec succès');
      },
      error: (err) => {
        this.isSaving.set(false);
        this.showToast('Erreur lors de l\'enregistrement', 'error');
      }
    });
  }

  triggerNow() {
    if (this.isTriggering() || this.settings().lastStatus === 'running') return;

    this.isTriggering.set(true);
    this.fraudService.triggerAnalysis().subscribe({
      next: () => {
        this.isTriggering.set(false);
        const current = this.settings();
        this.settings.set({
          ...current,
          lastStatus: 'running'
        });
        
        this.showToast('Analyse lancée avec succès', 'success');
        
        // Refresh settings after a while to see if lastRun updated
        setTimeout(() => this.ngOnInit(), 5000);
      },
      error: (err) => {
        this.isTriggering.set(false);
        this.showToast('Échec du lancement de l\'analyse', 'error');
      }
    });
  }


  goBack() {
    this.router.navigate(['/']);
  }
}
