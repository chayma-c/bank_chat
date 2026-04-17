import {
  Component, Input, Output, EventEmitter,
  OnInit, signal, computed
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule   } from '@angular/forms';
import { FraudRule, RuleFormData, RiskDomain, Severity } from './fraud-rule.model';

@Component({
  selector:    'app-rule-modal',
  standalone:  true,
  imports:     [CommonModule, FormsModule],
  templateUrl: './rule-modal.component.html',
  styleUrl:    './rule-modal.component.css',
})
export class RuleModalComponent implements OnInit {
  @Input()  mode:    'create' | 'edit' | 'delete' = 'create';
  @Input()  rule:    FraudRule | null = null;
  @Input()  saving:  boolean = false;          // ← Input manquant — cause du build error
  @Output() saved   = new EventEmitter<RuleFormData>();
  @Output() deleted = new EventEmitter<void>();
  @Output() closed  = new EventEmitter<void>();

  readonly domains:    RiskDomain[] = ['VELOCITY', 'LIMIT', 'GEOGRAPHIC', 'AML', 'BEHAVIORAL'];
  readonly severities: Severity[]   = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

  form = signal<RuleFormData>({
    name:          '',
    domain:        'VELOCITY',
    trigger:       '',
    triggerDetail: '',
    points:        10,
    severity:      'MEDIUM',
    active:        true,
    description:   '',
  });

  pointsPreview = computed(() => {
    const p = this.form().points;
    if (p >= 25) return { label: 'High impact',   cls: 'impact-high' };
    if (p >= 15) return { label: 'Medium impact', cls: 'impact-med'  };
    return             { label: 'Low impact',     cls: 'impact-low'  };
  });

  scoreBarWidth = computed(() =>
    Math.min(100, Math.round(this.form().points / 35 * 100)) + '%'
  );

  ngOnInit(): void {
    if (this.rule && this.mode === 'edit') {
      this.form.set({
        name:          this.rule.name,
        domain:        this.rule.domain,
        trigger:       this.rule.trigger,
        triggerDetail: this.rule.triggerDetail,
        points:        this.rule.points,
        severity:      this.rule.severity,
        active:        this.rule.active,
        description:   this.rule.description,
      });
    }
  }

  updateField<K extends keyof RuleFormData>(key: K, value: RuleFormData[K]): void {
    this.form.update(f => ({ ...f, [key]: value }));
  }

  submit(): void {
    const f = this.form();
    if (!f.name.trim() || !f.trigger.trim()) return;
    this.saved.emit(f);
  }

  confirmDelete(): void {
    this.deleted.emit();
  }

  close(): void {
    this.closed.emit();
  }

  get isCreate(): boolean { return this.mode === 'create'; }
  get isEdit():   boolean { return this.mode === 'edit';   }
  get isDelete(): boolean { return this.mode === 'delete'; }

  get modalTitle(): string {
    return this.isCreate ? 'Create new rule'
         : this.isEdit   ? 'Edit rule'
         :                 'Delete rule';
  }
}