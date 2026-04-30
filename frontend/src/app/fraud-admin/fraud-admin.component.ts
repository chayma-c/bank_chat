import {
  Component, OnInit, signal, computed, inject
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule   } from '@angular/forms';
import { RouterModule  } from '@angular/router';
import { FraudRulesService }  from './fraud-rules.service';
import { RuleModalComponent } from './rule-modal.component';
import { FraudRule, RuleFormData, RiskDomain } from './fraud-rule.model';
import { DecisionLogsService, DecisionLog, DecisionLogStats } from '../services/decision-logs.service';
type ModalMode = 'create' | 'edit' | 'delete';

@Component({
  selector:    'app-fraud-admin',
  standalone:  true,
  imports:     [CommonModule, FormsModule, RouterModule, RuleModalComponent],
  templateUrl: './fraud-admin.component.html',
  styleUrl:    './fraud-admin.component.css',
})
export class FraudAdminComponent implements OnInit {
  private svc = inject(FraudRulesService);

  // ── Filters ───────────────────────────────────────────────────────────────
  domainFilter = signal<string>('');
  statusFilter = signal<string>('');
  searchQuery  = signal<string>('');

  // ── Modal ─────────────────────────────────────────────────────────────────
  modalMode    = signal<ModalMode | null>(null);
  selectedRule = signal<FraudRule | null>(null);
  saving       = signal<boolean>(false);

  // ── Toast ─────────────────────────────────────────────────────────────────
  toast = signal<{ msg: string; type: 'success' | 'error' } | null>(null);

  // ── Exposed from service ──────────────────────────────────────────────────
  readonly metrics     = this.svc.metrics;
  readonly loading     = this.svc.loading;
  readonly loadingError = this.svc.error;

  readonly filteredRules = computed(() =>
    this.svc.filterRules(this.domainFilter(), this.statusFilter(), this.searchQuery())
  );

  readonly domains: RiskDomain[] = ['VELOCITY', 'LIMIT', 'GEOGRAPHIC', 'AML', 'BEHAVIORAL'];

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  ngOnInit(): void {
    this.svc.loadRules();
  }

  // ── Modal open/close ──────────────────────────────────────────────────────
  openCreate(): void {
    this.selectedRule.set(null);
    this.modalMode.set('create');
  }

  openEdit(rule: FraudRule, event: Event): void {
    event.stopPropagation();
    this.selectedRule.set(rule);
    this.modalMode.set('edit');
  }

  openDelete(rule: FraudRule, event: Event): void {
    event.stopPropagation();
    this.selectedRule.set(rule);
    this.modalMode.set('delete');
  }

  closeModal(): void {
    this.modalMode.set(null);
    this.selectedRule.set(null);
    this.saving.set(false);
  }

  // ── CRUD handlers ─────────────────────────────────────────────────────────
  onSave(data: RuleFormData): void {
    this.saving.set(true);
    const mode = this.modalMode();

    if (mode === 'create') {
      this.svc.createRule(data).subscribe({
        next:  () => { this.closeModal(); this.showToast('Rule created successfully', 'success'); },
        error: (msg: string) => { this.saving.set(false); this.showToast(msg, 'error'); },
      });

    } else if (mode === 'edit' && this.selectedRule()) {
      this.svc.updateRule(this.selectedRule()!.id, data).subscribe({
        next:  () => { this.closeModal(); this.showToast('Rule updated successfully', 'success'); },
        error: (msg: string) => { this.saving.set(false); this.showToast(msg, 'error'); },
      });
    }
  }

  onDelete(): void {
    const rule = this.selectedRule();
    if (!rule) return;
    this.saving.set(true);

    this.svc.deleteRule(rule.id).subscribe({
      next:  () => { this.closeModal(); this.showToast('Rule deleted', 'success'); },
      error: (msg: string) => { this.saving.set(false); this.showToast(msg, 'error'); },
    });
  }

  toggleRule(rule: FraudRule, active: boolean): void {
    this.svc.toggleRule(rule.id, active);
  }

  retryLoad(): void {
    this.svc.loadRules();
  }

  // ── Visual helpers ────────────────────────────────────────────────────────
  impactBarWidth(pts: number): string {
    return Math.min(100, Math.round(pts / 35 * 100)) + '%';
  }

  impactClass(pts: number): string {
    if (pts >= 25) return 'impact-high';
    if (pts >= 15) return 'impact-med';
    return 'impact-low';
  }

  domainClass(d: string): string {
    return 'domain-' + d.toLowerCase();
  }

  trackById(_: number, rule: FraudRule): string {
    return rule.id;
  }

  private showToast(msg: string, type: 'success' | 'error'): void {
    this.toast.set({ msg, type });
    setTimeout(() => this.toast.set(null), 3500);
  }
}