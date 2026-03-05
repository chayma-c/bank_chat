import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { KeycloakService } from '../auth/keycloak.service';

export interface ChatRequest {
  user_id: string;
  session_id?: string;
  message: string;
}

export interface ChatResponse {
  session_id: string;
  response: string;
  agent_used: string;
}

export interface StreamEvent {
  token?: string;
  agent?: string;
  done?: boolean;
  session_id?: string;
  error?: string;
}

export interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  agent_used?: string;
  created_at: string;
}

export interface Conversation {
  session_id: string;
  user_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  messages: Message[];
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly base = environment.apiBaseUrl;
  private readonly http = inject(HttpClient);
  private readonly keycloak = inject(KeycloakService);

  sendMessage(payload: ChatRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.base}/chat/`, payload);
  }

  streamMessage(payload: ChatRequest): Observable<StreamEvent> {
    return new Observable((observer) => {
      const controller = new AbortController();

      fetch(`${this.base}/chat/stream/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(this.keycloak.token
            ? { Authorization: `Bearer ${this.keycloak.token}` }
            : {}),
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })
        .then((response) => {
          if (!response.ok) {
            observer.error(new Error(`HTTP ${response.status}`));
            return;
          }
          const reader = response.body!.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          const read = (): void => {
            reader.read().then(({ done, value }) => {
              if (done) { observer.complete(); return; }
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() ?? '';
              for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('data: ')) {
                  try {
                    const evt: StreamEvent = JSON.parse(trimmed.slice(6));
                    observer.next(evt);
                    if (evt.done || evt.error) { observer.complete(); return; }
                  } catch { /* skip malformed lines */ }
                }
              }
              read();
            }).catch((err) => observer.error(err));
          };
          read();
        })
        .catch((err) => {
          if (err.name !== 'AbortError') observer.error(err);
        });

      return () => controller.abort();
    });
  }

  getConversations(userId: string): Observable<Conversation[]> {
    return this.http.get<Conversation[]>(`${this.base}/conversations/?user_id=${userId}`);
  }

  getConversation(sessionId: string): Observable<Conversation> {
    return this.http.get<Conversation>(`${this.base}/conversations/${sessionId}/`);
  }

  deleteConversation(sessionId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/conversations/${sessionId}/`);
  }
}