import { Injectable, inject, signal, computed } from '@angular/core';
import { HttpClient, HttpErrorResponse }         from '@angular/common/http';
import { Observable, throwError, tap }           from 'rxjs';
import { catchError, map }                       from 'rxjs/operators';
import { environment }                           from '../../environments/environment';
import { FraudRule, RuleFormData, FraudMetrics } from './fraud-rule.model';

@Injectable({ providedIn: 'root' })
export class FraudRulesService {
  private readonly http = inject(HttpClient);

  // Base URL of your FastAPI fraud service — set in environment.ts (fraudUrl)
  private readonly base = `${environment.fraudUrl}/rules/`;

  // ── Reactive state ────────────────────────────────────────────────────────
  private _rules   = signal<FraudRule[]>([]);
  private _loading = signal<boolean>(false);
  private _error   = signal<string | null>(null);

  readonly rules   = this._rules.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error   = this._error.asReadonly();

  readonly activeCount = computed(() => this._rules().filter(r => r.active).length);

  readonly metrics = computed<FraudMetrics>(() => ({
    activeProtocols:  this.activeCount(),
    velocityFlags24h: 1892,        // replace with a real /stats endpoint later
    avgPrecisionRate: 98.4,
    totalRules:       this._rules().length,
  }));

  // ── Load all rules from backend ───────────────────────────────────────────
  loadRules(): void {
    this._loading.set(true);
    this._error.set(null);

    this.http.get<FraudRule[]>(this.base).pipe(
      catchError(err => this.handleError(err))
    ).subscribe({
      next:  rules => { this._rules.set(rules); this._loading.set(false); },
      error: msg   => { this._error.set(msg);   this._loading.set(false); },
    });
  }

  // ── Create ────────────────────────────────────────────────────────────────
  createRule(data: RuleFormData): Observable<FraudRule> {
    return this.http.post<FraudRule>(this.base, data).pipe(
      tap(rule => this._rules.update(rs => [...rs, rule])),
      catchError(err => this.handleError(err))
    );
  }

  // ── Update (full) ─────────────────────────────────────────────────────────
  updateRule(id: string, data: Partial<FraudRule>): Observable<FraudRule> {
    return this.http.put<FraudRule>(`${this.base}/${id}`, data).pipe(
      tap(updated => this._rules.update(rs => rs.map(r => r.id === id ? updated : r))),
      catchError(err => this.handleError(err))
    );
  }

  // ── Toggle active — PATCH (lightweight, no full reload) ───────────────────
  toggleRule(id: string, active: boolean): void {
    // Optimistic UI update first
    this._rules.update(rs => rs.map(r => r.id === id ? { ...r, active } : r));

    this.http.patch<FraudRule>(`${this.base}/${id}`, { active }).pipe(
      catchError(err => {
        // Rollback on failure
        this._rules.update(rs => rs.map(r => r.id === id ? { ...r, active: !active } : r));
        return this.handleError(err);
      })
    ).subscribe();
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  deleteRule(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`).pipe(
      tap(() => this._rules.update(rs => rs.filter(r => r.id !== id))),
      catchError(err => this.handleError(err))
    );
  }

  // ── Filter (pure — no HTTP) ───────────────────────────────────────────────
  filterRules(domain: string, status: string, search: string): FraudRule[] {
    return this._rules().filter(r => {
      if (domain && r.domain !== domain)           return false;
      if (status === 'active' && !r.active)        return false;
      if (status === 'paused' &&  r.active)        return false;
      if (search) {
        const q = search.toLowerCase();
        if (!r.name.toLowerCase().includes(q) &&
            !r.id.toLowerCase().includes(q)   &&
            !r.trigger.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }

  // ── Error handling ────────────────────────────────────────────────────────
  private handleError(err: HttpErrorResponse): Observable<never> {
    let msg = 'An unexpected error occurred';
    if (err.status === 0)   msg = 'Cannot reach the fraud service. Check network / CORS.';
    if (err.status === 404) msg = 'Rule not found.';
    if (err.status === 422) msg = 'Validation error: ' + JSON.stringify(err.error?.detail ?? '');
    if (err.status >= 500)  msg = 'Server error. Please try again.';
    console.error('[FraudRulesService]', err);
    return throwError(() => msg);
  }
}