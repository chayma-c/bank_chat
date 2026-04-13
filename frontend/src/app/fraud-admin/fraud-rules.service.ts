import { Injectable, inject, signal, computed } from '@angular/core';
import { HttpClient }                            from '@angular/common/http';
import { Observable, of, tap, catchError }      from 'rxjs';
import { environment }                           from '../../environments/environment';
import { FraudRule, RuleFormData, FraudMetrics } from './fraud-rule.model';

// ── Default rules seeded from rules.py / scoring.py ──────────────────────────
const DEFAULT_RULES: FraudRule[] = [
  {
    id: 'RL-001-LAR', name: 'Large or round amount',
    domain: 'LIMIT', severity: 'HIGH',
    trigger: 'Amount > 3,000 TND',
    triggerDetail: 'Or suspicious round amounts (999, 1000, 5000…)',
    points: 35, active: true,
    description: 'Flags transactions above threshold or with suspicious round amounts used in fund-smuggling or card-testing fraud.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-002-SIB', name: 'Suspicious IBAN check',
    domain: 'AML', severity: 'HIGH',
    trigger: 'Client / counterparty IBAN in blacklist',
    triggerDetail: 'Known structuring or ML-prone accounts',
    points: 35, active: true,
    description: 'Checks client and counterparty IBANs against the configured suspicious IBAN list.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-003-STR', name: 'Structuring pattern (AML)',
    domain: 'AML', severity: 'CRITICAL',
    trigger: '3+ txs of 850–950 TND within 24h',
    triggerDetail: 'Same client IBAN in 24-hour sliding window',
    points: 25, active: true,
    description: 'Classic AML structuring detection — multiple near-threshold amounts to avoid reporting thresholds.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-004-NGT', name: 'Night transfer alert',
    domain: 'VELOCITY', severity: 'MEDIUM',
    trigger: 'P2P / INTL transfer between 00:00–05:00',
    triggerDetail: 'Unusual hour for high-value transfers',
    points: 10, active: true,
    description: 'Flags P2P and international transfers made during night hours when normal users rarely operate.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-005-FIP', name: 'Foreign IP detection',
    domain: 'GEOGRAPHIC', severity: 'HIGH',
    trigger: 'IP starts with 185.230.x.x',
    triggerDetail: '+ amount > 2,000 TND or customer risk score ≥ 70',
    points: 25, active: true,
    description: 'Detects transactions from known foreign IP ranges combined with high-risk account context.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-006-MCC', name: 'High-risk merchant (MCC)',
    domain: 'BEHAVIORAL', severity: 'MEDIUM',
    trigger: 'MCC 5541 / 5999 / 5311 and amount > 1,500 TND',
    triggerDetail: 'No recent pattern for this MCC on account',
    points: 10, active: true,
    description: 'Flags high-value purchases at merchant category codes statistically linked to fraud.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-007-BAL', name: 'Balance drain pattern',
    domain: 'BEHAVIORAL', severity: 'HIGH',
    trigger: 'Amount > 80% of account current balance',
    triggerDetail: 'Moving most of balance in one transaction',
    points: 10, active: true,
    description: 'Detects transactions that drain most of the account balance in a single operation.',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'RL-008-ALT', name: 'Repeated alerts',
    domain: 'VELOCITY', severity: 'HIGH',
    trigger: '3+ ALERTED transactions in last 7 days',
    triggerDetail: 'Same client IBAN recurring flags',
    points: 20, active: false,
    description: 'Detects ongoing risk profiles from repeated alert status on the same IBAN over a 7-day window.',
    createdAt: new Date().toISOString(),
  },
];

@Injectable({ providedIn: 'root' })
export class FraudRulesService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/fraud/rules`;

  // ── Local state (falls back to defaults when API unavailable) ────────────
  private _rules = signal<FraudRule[]>(DEFAULT_RULES);

  readonly rules          = this._rules.asReadonly();
  readonly activeCount    = computed(() => this._rules().filter(r => r.active).length);
  readonly totalPoints    = computed(() => this._rules().filter(r => r.active).reduce((s, r) => s + r.points, 0));
  readonly metrics        = computed<FraudMetrics>(() => ({
    activeProtocols:  this.activeCount(),
    velocityFlags24h: 1892,
    avgPrecisionRate: 98.4,
    totalRules:       this._rules().length,
  }));

  // ── Load from API (graceful fallback to defaults) ────────────────────────
  loadRules(): void {
    this.http.get<FraudRule[]>(this.base).pipe(
      catchError(() => of(DEFAULT_RULES))
    ).subscribe(rules => this._rules.set(rules));
  }

  // ── Create ────────────────────────────────────────────────────────────────
  createRule(data: RuleFormData): Observable<FraudRule> {
    const initials = data.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 3);
    const newRule: FraudRule = {
      ...data,
      id:        `RL-${String(Date.now()).slice(-6)}-${initials}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    return this.http.post<FraudRule>(this.base, newRule).pipe(
      catchError(() => of(newRule)),
      tap(rule => this._rules.update(rs => [...rs, rule]))
    );
  }

  // ── Update ────────────────────────────────────────────────────────────────
  updateRule(id: string, data: Partial<FraudRule>): Observable<FraudRule> {
    const current = this._rules().find(r => r.id === id)!;
    const updated: FraudRule = { ...current, ...data, updatedAt: new Date().toISOString() };

    return this.http.put<FraudRule>(`${this.base}/${id}`, updated).pipe(
      catchError(() => of(updated)),
      tap(rule => this._rules.update(rs => rs.map(r => r.id === id ? rule : r)))
    );
  }

  // ── Toggle active ─────────────────────────────────────────────────────────
  toggleRule(id: string, active: boolean): void {
    this._rules.update(rs => rs.map(r => r.id === id ? { ...r, active } : r));
    this.http.patch(`${this.base}/${id}`, { active }).pipe(catchError(() => of(null))).subscribe();
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  deleteRule(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`).pipe(
      catchError(() => of(undefined)),
      tap(() => this._rules.update(rs => rs.filter(r => r.id !== id)))
    );
  }

  // ── Filter helper ─────────────────────────────────────────────────────────
  filterRules(domain: string, status: string, search: string): FraudRule[] {
    return this._rules().filter(r => {
      if (domain && r.domain !== domain)                          return false;
      if (status === 'active'  && !r.active)                     return false;
      if (status === 'paused'  &&  r.active)                     return false;
      if (search) {
        const q = search.toLowerCase();
        if (!r.name.toLowerCase().includes(q) &&
            !r.id.toLowerCase().includes(q)   &&
            !r.trigger.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }
}
