// ── Models ────────────────────────────────────────────────────────────────────

export type RiskDomain = 'VELOCITY' | 'LIMIT' | 'GEOGRAPHIC' | 'AML' | 'BEHAVIORAL';
export type Severity   = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type RiskLevel  = 'APPROVED' | 'REVIEW' | 'BLOCK';

export interface FraudRule {
  id:            string;
  name:          string;
  domain:        RiskDomain;
  trigger:       string;
  triggerDetail: string;
  points:        number;
  severity:      Severity;
  active:        boolean;
  description:   string;
  createdAt?:    string;
  updatedAt?:    string;
}

export interface RuleFormData {
  name:          string;
  domain:        RiskDomain;
  trigger:       string;
  triggerDetail: string;
  points:        number;
  severity:      Severity;
  active:        boolean;
  description:   string;
}

export interface FraudMetrics {
  activeProtocols:   number;
  velocityFlags24h:  number;
  avgPrecisionRate:  number;
  totalRules:        number;
}
