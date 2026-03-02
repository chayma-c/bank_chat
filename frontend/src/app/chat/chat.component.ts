import {
  Component, OnInit, AfterViewChecked,
  ViewChild, ElementRef, signal, computed
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatService, Conversation, Message } from '../services/chat.service';
import { v4 as uuidv4 } from 'uuid';

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

  readonly userId = 'user-demo';

  sessionId = signal<string>(uuidv4());
  messages  = signal<LocalMessage[]>([]);
  userInput = signal<string>('');
  sending   = signal<boolean>(false);
  sidebarOpen = signal<boolean>(true);
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

    // Append user message instantly
    this.messages.update(msgs => [...msgs, { role: 'user', content: text }]);
    // Append empty bot bubble — will be filled token by token
    this.messages.update(msgs => [...msgs, { role: 'assistant', content: '', loading: true }]);

    this.chatService.streamMessage({
      user_id:    this.userId,
      session_id: this.sessionId(),
      message:    text
    }).subscribe({
      next: (evt) => {
        if (evt.error) {
          this.messages.update(msgs => {
            const updated = [...msgs];
            updated[updated.length - 1] = { role: 'assistant', content: 'An error occurred. Please try again.' };
            return updated;
          });
          this.sending.set(false);
          return;
        }

        if (evt.token) {
          // Append token to the last bubble in real time
          this.messages.update(msgs => {
            const updated = [...msgs];
            const last    = updated[updated.length - 1];
            updated[updated.length - 1] = {
              ...last,
              content: last.content + evt.token,
              loading: false,
            };
            return updated;
          });
        }

        if (evt.done) {
          // Finalize: attach agent badge, stop spinner, refresh sidebar
          this.messages.update(msgs => {
            const updated = [...msgs];
            const last    = updated[updated.length - 1];
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
          updated[updated.length - 1] = { role: 'assistant', content: 'Connection error. Please try again.' };
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
    if (!conv.messages?.length) return 'New conversation';
    const last = conv.messages[conv.messages.length - 1];
    return last.content.length > 40 ? last.content.slice(0, 40) + '…' : last.content;
  }

  formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
  }

  toggleSidebar(): void {
    this.sidebarOpen.update(v => !v);
  }

  trackBySession(_: number, conv: Conversation): string {
    return conv.session_id;
  }
}
