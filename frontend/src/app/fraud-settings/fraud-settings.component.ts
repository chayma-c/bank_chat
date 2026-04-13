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
    this.fraudService.getSettings().subscribe(data => {
      this.settings.set({
        ...data,
        time: data.time || '00:00',
        dayOfWeek: data.dayOfWeek ?? 1
      });
    });
  }

  saveSettings() {
    this.isSaving.set(true);
    this.fraudService.updateSettings(this.settings()).subscribe(() => {
      setTimeout(() => {
        this.isSaving.set(false);
        this.showSuccess.set(true);
        setTimeout(() => this.showSuccess.set(false), 3000);
      }, 800);
    });
  }

  triggerNow() {
    this.isTriggering.set(true);
    this.fraudService.triggerAnalysis().subscribe(() => {
      setTimeout(() => {
        this.isTriggering.set(false);
        // Could update lastRun here
        const current = this.settings();
        this.settings.set({
          ...current,
          lastRun: new Date().toISOString(),
          lastStatus: 'running'
        });
      }, 1500);
    });
  }

  goBack() {
    this.router.navigate(['/']);
  }
}
