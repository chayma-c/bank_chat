import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

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
  private readonly base = 'http://localhost:8000/api/v1/chatbot';

  constructor(private http: HttpClient) {}

  sendMessage(payload: ChatRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.base}/chat/`, payload);
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
