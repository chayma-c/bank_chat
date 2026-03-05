import {
  Component, OnInit, AfterViewChecked,
  ViewChild, ElementRef, signal, computed, inject
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatService, Conversation } from '../services/chat.service';
import { v4 as uuidv4 } from 'uuid';
import { KeycloakService } from '../auth/keycloak.service';

interface LocalMessage {
  role: 'user' | 'assistant';
  content: string;
  agent_used?: string;
  loading?: boolean;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css'
})
export class ChatComponent implements OnInit, AfterViewChecked {
  @ViewChild('messagesEnd') private messagesEnd!: ElementRef;

  private keycloak = inject(KeycloakService);
  readonly userId = this.keycloak.userId;

  sessionId     = signal<string>(uuidv4());
  messages      = signal<LocalMessage[]>([]);
  userInput     = signal<string>('');
  sending       = signal<boolean>(false);
  sidebarOpen   = signal<boolean>(true);
  conversations = signal<Conversation[]>([]);
  activeSession = signal<string>('');

  isNewChat = computed(() => this.messages().length === 0);

  constructor(private chatService: ChatService) {}

  ngOnInit(): void {
    this.loadConversations();
  }

  ngAfterViewChecked(): void {
    this.scrollToBottom();
  }

  private scrollToBottom(): void {
    try {
      this.messagesEnd.nativeElement.scrollIntoView({ behavior: 'smooth' });
    } catch {}
  }

  loadConversations(): void {
    this.chatService.getConversations(this.userId).subscribe({
      next: (convs) => this.conversations.set(convs),
      error: () => {}
    });
  }

  newChat(): void {
    this.sessionId.set(uuidv4());
    this.messages.set([]);
    this.activeSession.set('');
  }

  loadConversation(sessionId: string): void {
    this.activeSession.set(sessionId);
    this.chatService.getConversation(sessionId).subscribe({
      next: (conv) => {
        this.sessionId.set(sessionId);
        this.messages.set(
          conv.messages.map(m => ({
            role: m.role,
            content: m.content,
            agent_used: m.agent_used ?? undefined
          }))
        );
      }
    });
  }

  deleteConversation(event: Event, sessionId: string): void {
    event.stopPropagation();
    this.chatService.deleteConversation(sessionId).subscribe({
      next: () => {
        this.loadConversations();
        if (this.activeSession() === sessionId) {
          this.newChat();
        }
      }
    });
  }

  send(): void {
    const text = this.userInput().trim();
    if (!text || this.sending()) return;

    this.userInput.set('');
    this.sending.set(true);

    // 1. Afficher le message utilisateur immédiatement
    this.messages.update(msgs => [...msgs, { role: 'user', content: text }]);

    // 2. Afficher une bulle vide avec loading pendant l'attente
    this.messages.update(msgs => [...msgs, { role: 'assistant', content: '', loading: true }]);

    // 3. Appel bloquant simple
    this.chatService.sendMessage({
      user_id:    this.userId,
      session_id: this.sessionId(),
      message:    text
    }).subscribe({
      next: (response) => {
        // 4. Remplacer la bulle loading par la vraie réponse
        this.messages.update(msgs => {
          const updated = [...msgs];
          updated[updated.length - 1] = {
            role:      'assistant',
            content:   response.response,
            agent_used: response.agent_used,
            loading:   false,
          };
          return updated;
        });
        this.sending.set(false);
        this.loadConversations();
      },
      error: () => {
        // 5. En cas d'erreur, remplacer la bulle par un message d'erreur
        this.messages.update(msgs => {
          const updated = [...msgs];
          updated[updated.length - 1] = {
            role:    'assistant',
            content: 'Une erreur est survenue. Veuillez réessayer.',
            loading: false,
          };
          return updated;
        });
        this.sending.set(false);
      }
    });
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  getPreview(conv: Conversation): string {
    if (!conv.messages?.length) return 'Nouvelle conversation';
    const last = conv.messages[conv.messages.length - 1];
    return last.content.length > 40 ? last.content.slice(0, 40) + '…' : last.content;
  }

  formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
  }

  toggleSidebar(): void {
    this.sidebarOpen.update(v => !v);
  }

  trackBySession(_: number, conv: Conversation): string {
    return conv.session_id;
  }
}