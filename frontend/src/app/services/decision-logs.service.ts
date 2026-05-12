// src/app/services/decision-logs.service.ts

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface TriggeredRule {
  rule:     string;
  domain:   string;
  points:   number;
  severity: string;
  details:  string;
}

export interface DecisionLog {
  id:                     string;
  created_at:             string;
  user_id:                string | null;
  session_id:             string | null;
  iban:                   string;
  transactions_count:     number;
  date_range:             string | null;
  score_behavioral:       number;
  score_aml:              number;
  score_final:            number;
  risk_level:             string;
  tracfin_required:       boolean;
  rules_triggered:        number;
  rules_evaluated:        number;
  triggered_rules_detail: TriggeredRule[];
  report_path:            string | null;
  download_url:           string | null;
  mail_sent:              boolean;
  mail_recipient:         string | null;
  mail_template:          string | null;
  mail_status:            string | null;
  mail_id:                string | null;
  llm_summary:            string;
  error:                  string | null;
}

export interface DecisionLogStats {
  total_analyses: number;
  block_count:    number;
  tracfin_count:  number;
  mailed_count:   number;
  avg_score:      number;
}

export interface DecisionLogsResponse {
  total:  number;
  limit:  number;
  offset: number;
  logs:   DecisionLog[];
}

@Injectable({ providedIn: 'root' })
export class DecisionLogsService {
  private http    = inject(HttpClient);
  private baseUrl = `${environment.fraudUrl}/decision-logs`;

  getLogs(params: {
    limit?:      number;
    offset?:     number;
    iban?:       string;
    risk_level?: string;
    mail_sent?:  boolean;
  } = {}): Observable<DecisionLogsResponse> {
    let p = new HttpParams();
    if (params.limit      != null) p = p.set('limit',      params.limit);
    if (params.offset     != null) p = p.set('offset',     params.offset);
    if (params.iban)                p = p.set('iban',       params.iban);
    if (params.risk_level)          p = p.set('risk_level', params.risk_level);
    if (params.mail_sent  != null)  p = p.set('mail_sent',  params.mail_sent);
    return this.http.get<DecisionLogsResponse>(this.baseUrl, { params: p });
  }

  getStats(): Observable<DecisionLogStats> {
    return this.http.get<DecisionLogStats>(`${this.baseUrl}/stats`);
  }

  getLog(id: string): Observable<DecisionLog> {
    return this.http.get<DecisionLog>(`${this.baseUrl}/${id}`);
  }
}