import {
  Component, OnInit, signal, computed, inject
} from '@angular/core';
import { CommonModule, DatePipe, SlicePipe } from '@angular/common';
import { interval, switchMap, takeWhile, take } from 'rxjs';
import { FormsModule  } from '@angular/forms';
import { RouterModule } from '@angular/router';
import {
  DecisionLogsService,
  DecisionLog,
  DecisionLogStats,
} from '../services/decision-logs.service';

@Component({
  selector:    'app-decision-logs',
  standalone:  true,
  imports:     [CommonModule, FormsModule, RouterModule, DatePipe, SlicePipe],
  templateUrl: './decision-logs.component.html',
  styleUrl:    './decision-logs.component.css',
})
export class DecisionLogsComponent implements OnInit {
  private svc = inject(DecisionLogsService);

  // ── Data ──────────────────────────────────────────────────────────────────
  logs        = signal<DecisionLog[]>([]);
  stats       = signal<DecisionLogStats | null>(null);
  loading     = signal<boolean>(false);
  total       = signal<number>(0);

  // ── Filters ───────────────────────────────────────────────────────────────
  riskFilter  = signal<string>('');
  mailFilter  = signal<string>('');   // '' | 'true' | 'false'
  ibanFilter  = signal<string>('');

  // ── Pagination ────────────────────────────────────────────────────────────
  offset      = signal<number>(0);
  readonly limit = 20;
  currentPage = computed(() => Math.floor(this.offset() / this.limit) + 1);

  // ── Selected log (detail drawer) ─────────────────────────────────────────
  selectedLog = signal<DecisionLog | null>(null);
  drawerOpen  = signal<boolean>(false);

  // ── Toast ─────────────────────────────────────────────────────────────────
  toast = signal<{ msg: string; type: 'success' | 'error' } | null>(null);

  readonly riskLevels = ['BLOCK', 'HOLD', 'REVIEW', 'APPROVED'];

  ngOnInit(): void {
    this.loadStats();
    this.loadLogs();
  }

  loadStats(): void {
    this.svc.getStats().subscribe({
      next:  (s) => this.stats.set(s),
      error: () => {},
    });
  }

  loadLogs(): void {
    this.loading.set(true);
    const mailSent = this.mailFilter() === 'true'  ? true
                   : this.mailFilter() === 'false' ? false
                   : undefined;
    this.svc.getLogs({
      limit:      this.limit,
      offset:     this.offset(),
      risk_level: this.riskFilter()  || undefined,
      iban:       this.ibanFilter()  || undefined,
      mail_sent:  mailSent,
    }).subscribe({
      next: (r) => {
        this.logs.set(r.logs);
        this.total.set(r.total);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.showToast('Failed to load decision logs', 'error');
      },
    });
  }

  applyFilters(): void {
    this.offset.set(0);
    this.loadLogs();
  }

  nextPage(): void {
    if (this.offset() + this.limit < this.total()) {
      this.offset.update(o => o + this.limit);
      this.loadLogs();
    }
  }

  prevPage(): void {
    if (this.offset() > 0) {
      this.offset.update(o => Math.max(0, o - this.limit));
      this.loadLogs();
    }
  }

  // ── Detail drawer ─────────────────────────────────────────────────────────
  drawerLoading = signal<boolean>(false);

  openDetail(log: DecisionLog): void {
    // 1. Show drawer immediately with the list snapshot (instant feedback)
    this.selectedLog.set(log);
    this.drawerOpen.set(true);
    this.drawerLoading.set(true);

    // 2. Fetch once immediately to get the most recent DB state
    this.svc.getLog(log.id).subscribe({
      next: (fresh) => {
        this.selectedLog.set(fresh);
        this.drawerLoading.set(false);

        // 3. If the fraud score justifies a mail (>=50 or TRACFIN)
        //    but mail_sent is still false — the PATCH from mail_agent
        //    may not have arrived yet. Poll every 2s for up to 20s.
        const shouldExpectMail = fresh.score_final >= 50 || fresh.tracfin_required;
        if (shouldExpectMail && !fresh.mail_sent) {
          this.pollForMailUpdate(log.id);
        }
      },
      error: () => { this.drawerLoading.set(false); },
    });
  }

  /**
   * Poll GET /decision-logs/{id} every 2 seconds until mail_sent = true
   * or until 10 attempts (20 seconds) — whichever comes first.
   * Stops automatically once the mail info is populated.
   */
  private pollForMailUpdate(logId: string, maxAttempts = 10): void {
    let attempts = 0;
    interval(2000).pipe(
      switchMap(() => this.svc.getLog(logId)),
      takeWhile((fresh) => {
        attempts++;
        if (fresh.mail_sent || fresh.mail_status === 'non_requis' || attempts >= maxAttempts) {
          this.selectedLog.set(fresh);
          return false;
        }
        return true;
      }),
    ).subscribe({
      next:     (fresh) => this.selectedLog.set(fresh),
      error:    ()      => { /* silent — keep showing last known state */ },
    });
  }

  refreshDrawer(): void {
    const log = this.selectedLog();
    if (!log) return;
    this.drawerLoading.set(true);
    this.svc.getLog(log.id).subscribe({
      next:  (fresh) => { this.selectedLog.set(fresh); this.drawerLoading.set(false); },
      error: () => { this.drawerLoading.set(false); },
    });
  }
 
  closeDrawer(): void {
    this.drawerOpen.set(false);
    setTimeout(() => this.selectedLog.set(null), 300);
  }

  // ── Visual helpers ────────────────────────────────────────────────────────
  riskClass(r: string): string {
    const map: Record<string, string> = {
      BLOCK:    'risk-block',
      HOLD:     'risk-hold',
      REVIEW:   'risk-review',
      APPROVED: 'risk-approved',
    };
    return map[r] ?? 'risk-review';
  }

  riskEmoji(r: string): string {
    return { BLOCK: '🔴', HOLD: '🟠', REVIEW: '🟡', APPROVED: '🟢' }[r] ?? '⚪';
  }

  scoreClass(s: number): string {
    if (s >= 80) return 'score-high';
    if (s >= 50) return 'score-med';
    return 'score-low';
  }

  mailStatusClass(log: DecisionLog): string {
    if (log.mail_status === 'non_requis') return 'mail-none';
    if (!log.mail_sent) return 'mail-pending';
    return log.mail_status === 'sent' ? 'mail-sent' : 'mail-failed';
  }

  mailLabel(log: DecisionLog): string {
    if (log.mail_status === 'non_requis') return 'Non requis';
    if (!log.mail_sent) return '—';
    return log.mail_status === 'sent' ? '✓ Envoyé' : '✗ Échec';
  }

  trackById(_: number, log: DecisionLog): string { return log.id; }

  // ── Mail type helpers ─────────────────────────────────────────────────────
  mailTypeClass(t: string | null): string {
    const map: Record<string, string> = {
      critical_alert:  'mtype-critical',
      fraud_alert:     'mtype-fraud',
      client_response: 'mtype-client',
      nightly_report:  'mtype-nightly',
    };
    return map[t ?? ''] ?? 'mtype-fraud';
  }

  mailTypeLabel(t: string | null): string {
    const map: Record<string, string> = {
      critical_alert:  '🔴 Critical alert',
      fraud_alert:     '⚠️ Fraud alert',
      client_response: '💬 Client response',
      nightly_report:  '🌙 Nightly report',
    };
    return map[t ?? ''] ?? t ?? '—';
  }

  private showToast(msg: string, type: 'success' | 'error'): void {
    this.toast.set({ msg, type });
    setTimeout(() => this.toast.set(null), 3500);
  }
}