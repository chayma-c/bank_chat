import {
  Component, OnInit, AfterViewChecked,
  ViewChild, ElementRef, signal, computed, inject, HostListener, effect
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
  @ViewChild('messagesArea') private messagesArea?: ElementRef<HTMLElement>;
  @ViewChild('inputField') private inputField?: ElementRef<HTMLTextAreaElement>;

  private stickToBottom = true;
  private readonly bottomThresholdPx = 48;

  private keycloak = inject(KeycloakService);
  readonly userId = this.keycloak.userId;
  readonly username = this.keycloak.username;
  readonly userInitial = this.keycloak.userInitial;
  readonly email = this.keycloak.email;

  sessionId = signal<string>(uuidv4());
  messages = signal<LocalMessage[]>([]);
  userInput = signal<string>('');
  sending = signal<boolean>(false);
  sidebarOpen = signal<boolean>(true);
  conversations = signal<Conversation[]>([]);
  activeSession = signal<string>('');

  /** Controls the visibility of the profile popover menu */
  profileMenuOpen = signal<boolean>(false);

  isNewChat = computed(() => this.messages().length === 0);

  constructor(private chatService: ChatService) {
    // Auto-focus the input whenever the model finishes responding
    effect(() => {
      if (!this.sending()) {
        // Use setTimeout to ensure the DOM has updated (textarea re-enabled)
        setTimeout(() => this.focusInput(), 0);
      }
    });
  }

  /** Focus the textarea programmatically */
  focusInput(): void {
    this.inputField?.nativeElement?.focus();
  }

  ngOnInit(): void {
    this.loadConversations();
  }

  ngAfterViewChecked(): void {
    if (this.stickToBottom) {
      this.scrollToBottom();
    }
  }

  /** Close profile menu when clicking anywhere outside */
  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (!target.closest('.sidebar-profile')) {
      this.profileMenuOpen.set(false);
    }
  }

  onMessagesScroll(): void {
    this.stickToBottom = this.isNearBottom();
  }

  private isNearBottom(): boolean {
    const container = this.messagesArea?.nativeElement;
    if (!container) {
      return true;
    }

    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    return distanceFromBottom <= this.bottomThresholdPx;
  }

  private scrollToBottom(): void {
    try {
      this.messagesEnd.nativeElement.scrollIntoView({ behavior: 'auto', block: 'end' });
    } catch { }
  }

  loadConversations(): void {
    this.chatService.getConversations(this.userId).subscribe({
      next: (convs) => this.conversations.set(convs),
      error: () => { }
    });
  }

  newChat(): void {
    this.sessionId.set(uuidv4());
    this.messages.set([]);
    this.activeSession.set('');
    this.stickToBottom = true;
  }

  loadConversation(sessionId: string): void {
    this.activeSession.set(sessionId);
    this.chatService.getConversation(sessionId).subscribe({
      next: (conv) => {
        this.sessionId.set(sessionId);
        this.stickToBottom = true;
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
    this.stickToBottom = true;

    this.messages.update(msgs => [...msgs, { role: 'user', content: text }]);
    this.messages.update(msgs => [...msgs, { role: 'assistant', content: '', loading: true }]);

    this.chatService.streamMessage({
      user_id: this.userId,
      session_id: this.sessionId(),
      message: text
    }).subscribe({
      next: (evt) => {
        if (evt.error) {
          this.messages.update(msgs => {
            const updated = [...msgs];
            updated[updated.length - 1] = {
              role: 'assistant',
              content: 'Une erreur est survenue. Veuillez réessayer.',
              loading: false,
            };
            return updated;
          });
          this.sending.set(false);
          return;
        }

        if (evt.token) {
          this.messages.update(msgs => {
            const updated = [...msgs];
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = {
              ...last,
              content: last.content + evt.token,
              loading: false,
            };
            return updated;
          });
        }

        if (evt.done) {
          this.messages.update(msgs => {
            const updated = [...msgs];
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, agent_used: evt.agent, loading: false };
            return updated;
          });
          this.sending.set(false);
          this.loadConversations();
        }
      },
      error: () => {
        this.messages.update(msgs => {
          const updated = [...msgs];
          updated[updated.length - 1] = {
            role: 'assistant',
            content: 'Erreur de connexion. Veuillez réessayer.',
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

  // ── Profile menu ──────────────────────────────────────────────

  toggleProfileMenu(): void {
    this.profileMenuOpen.update(v => !v);
  }

  logout(): void {
    this.keycloak.logout();
  }
}