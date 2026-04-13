import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { environment } from '../../environments/environment';

export interface FraudSettings {
  frequency: 'manual' | 'daily' | 'weekly';
  time?: string; // HH:mm
  dayOfWeek?: number; // 0-6
  lastRun?: string;
  lastStatus?: 'success' | 'failure' | 'running';
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
    // For now, return mock data since backend is not yet implemented
    return of({
      frequency: 'daily',
      time: '02:00',
      lastRun: new Date().toISOString(),
      lastStatus: 'success'
    });
  }

  /**
   * Update fraud agent settings
   */
  updateSettings(settings: FraudSettings): Observable<any> {
    console.log('Pushing settings to backend:', settings);
    return of({ status: 'updated' });
    // return this.http.post(`${this.apiUrl}/settings`, settings);
  }

  /**
   * Manually trigger the fraud analysis
   */
  triggerAnalysis(): Observable<any> {
    console.log('Triggering manual analysis...');
    return of({ status: 'triggered' });
    // return this.http.post(`${this.apiUrl}/trigger`, {});
  }
}
