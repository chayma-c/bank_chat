import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { environment } from '../../environments/environment';

export interface FraudSettings {
  frequency: 'manual' | 'daily' | 'weekly';
  time?: string; // HH:mm
  dayOfWeek?: number; // 0-6
  lastRun?: string;
  lastStatus?: 'success' | 'failure' | 'running' | 'ready';
}

@Injectable({
  providedIn: 'root'
})
export class FraudService {
  private http = inject(HttpClient);
  private apiUrl = environment.fraudUrl;

  /**
   * Fetch current fraud agent settings
   */
  getSettings(): Observable<FraudSettings> {
    return this.http.get<FraudSettings>(`${this.apiUrl}/settings`);
  }

  /**
   * Update fraud agent settings
   */
  updateSettings(settings: FraudSettings): Observable<any> {
    return this.http.post(`${this.apiUrl}/settings`, settings);
  }

  /**
   * Manually trigger the fraud analysis
   */
  triggerAnalysis(): Observable<any> {
    return this.http.post(`${this.apiUrl}/trigger`, {});
  }

}
