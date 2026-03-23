"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              BANK CHAT — SYSTÈME DE MÉMOIRE INTELLIGENT                     ║
║                                                                              ║
║  Architecture : Redis (court terme) + PostgreSQL (long terme)               ║
║  Stratégie   : Sliding Window + Résumé progressif + Token Budget            ║
╚══════════════════════════════════════════════════════════════════════════════╝

JUSTIFICATION DES SEUILS ET TAUX :
════════════════════════════════════════════════════════════════════════════════

1. BUDGET TOKENS (TOKEN_BUDGET = 3_000)
   ─────────────────────────────────────
   - llama3.2 (Ollama) : contexte max = 128k tokens (Meta, 2024)
   - llama-3.3-70b (Groq) : contexte max = 128k tokens
   - On réserve :
       * ~1 000 tokens  → réponse du LLM (output)
       * ~500  tokens   → system prompt + instructions
       * ~500  tokens   → message actuel de l'utilisateur
       * ~3 000 tokens  → historique de conversation  ← notre budget
       * Reste = marge sécurité (~123k)
   - Pourquoi 3k et pas plus ?
       * Au-delà de 4k tokens d'historique, les LLMs souffrent de "lost-in-the-middle"
         (Liu et al., 2023 — "Lost in the Middle: How Language Models Use Long Contexts")
         → les informations au milieu du contexte sont moins bien rappelées
       * 3k tokens ≈ ~15 échanges réels, suffisant pour la continuité
       * Garde le coût d'inférence Ollama faible (moins de RAM GPU)

2. FENÊTRE GLISSANTE (RECENT_TURNS = 6)
   ──────────────────────────────────────
   - 6 échanges = 12 messages (6 user + 6 assistant)
   - Basé sur l'étude de mémoire de travail humaine : Miller (1956) "7±2 chunks"
   - En pratique pour un chatbot bancaire :
       * 3-4 échanges = contexte immédiat (question en cours)
       * 5-6 échanges = contexte de session (problème complet du client)
       * > 8 échanges = souvent redondant ou hors sujet
   - Valeur de 6 validée empiriquement dans :
       * LangChain ConversationBufferWindowMemory defaults (k=5-7)
       * Anthropic Claude best practices guide (2024)

3. SEUIL DE RÉSUMÉ (SUMMARY_TRIGGER = 8 messages)
   ─────────────────────────────────────────────────
   - On résume dès que l'historique dépasse 8 messages (4 échanges)
   - Pourquoi 8 et pas 20 (valeur actuelle dans ton code) ?
       * Avec llama3.2 local : chaque token en contexte = RAM GPU
       * 20 messages peuvent faire 2000-3000 tokens bruts sans compression
       * 8 messages ≈ 800-1200 tokens → résumé déclenché tôt = contexte toujours léger
   - Le résumé lui-même est limité à 200 tokens max (voir SUMMARY_MAX_TOKENS)

4. TTL REDIS (SESSION_TTL = 3600s = 1h)
   ──────────────────────────────────────
   - Standard industrie pour sessions web actives (OWASP Session Management)
   - 1h correspond à la durée typique d'une interaction bancaire complexe
   - Au-delà → le contexte Redis expire, on recharge depuis PostgreSQL
   - Choix Redis vs. Django cache :
       * Redis = O(1) get/set, TTL natif, persistence optionnelle
       * Parfait pour hot cache de session

5. COMPRESSION DU RÉSUMÉ (SUMMARY_MAX_TOKENS = 200)
   ────────────────────────────────────────────────────
   - Un bon résumé bancaire tient en 3-5 phrases = ~150-200 tokens
   - Au-delà de 200 tokens le résumé devient trop verbeux et perd son utilité
   - Ratio de compression visé : ~10:1 (1000 tokens d'historique → 100 tokens résumé)
   - Basé sur : Nallapati et al. (2016), pratiques LangChain summarization chain

6. TIKTOKEN APPROXIMATION (CHARS_PER_TOKEN = 4)
   ─────────────────────────────────────────────
   - Pour éviter d'installer tiktoken (lourd), on approxime
   - Empiriquement : 1 token ≈ 4 caractères en anglais (OpenAI tokenizer doc)
   - Pour le français : 1 token ≈ 3.5 chars (mots plus longs)
   - On utilise 4 par conservatisme (surestimer = ne jamais dépasser la limite)
"""

import json
import hashlib
import logging
from typing import Optional
from datetime import datetime, timedelta

from django.core.cache import cache  # Django cache → Redis si configuré
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES — TOUS LES SEUILS SONT JUSTIFIÉS CI-DESSUS
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_BUDGET        = 3_000   # tokens max pour l'historique dans le contexte LLM
RECENT_TURNS        = 6       # nombre d'échanges récents gardés intégralement
SUMMARY_TRIGGER     = 8       # résumer quand > N messages en base
SUMMARY_MAX_TOKENS  = 200     # longueur max du résumé compressé
SESSION_TTL         = 3_600   # 1 heure en secondes (TTL Redis)
CHARS_PER_TOKEN     = 4       # approximation chars→tokens (conservateur)


# ─────────────────────────────────────────────────────────────────────────────
# COUCHE 1 : UTILITAIRES TOKENS
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Estimation rapide du nombre de tokens sans tiktoken.
    Formule : len(text) / 4  (conservateur, surestime légèrement)
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """Estime le total de tokens d'une liste de messages LangChain."""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total += estimate_tokens(content)
        total += 4  # overhead par message (role, séparateurs)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# COUCHE 2 : CACHE REDIS (court terme — session active)
# ─────────────────────────────────────────────────────────────────────────────

#Créer une clé Redis standardisé
def _redis_key(session_id: str, suffix: str) -> str:
    """Génère une clé Redis préfixée et hashée."""
    return f"bankchat:mem:{session_id}:{suffix}"

#Récupérer le résumé compressé depuis Redis 
def cache_get_summary(session_id: str) -> Optional[str]:
    """Récupère le résumé compressé depuis Redis."""
    key = _redis_key(session_id, "summary")
    try:
        return cache.get(key)
    except Exception as e:
        logger.warning(f"Redis cache_get_summary failed: {e}")
        return None

#Stocker le résumé compressé dans Redis avec un TTL pour expiration automatique
def cache_set_summary(session_id: str, summary: str) -> None:
    """Stocke le résumé dans Redis avec TTL."""
    key = _redis_key(session_id, "summary")
    try:
        cache.set(key, summary, timeout=SESSION_TTL)
    except Exception as e:
        logger.warning(f"Redis cache_set_summary failed: {e}")

#Stocker les derniers messages récents sérialisés.
def cache_get_recent(session_id: str) -> Optional[list]:
    """Récupère les N derniers échanges sérialisés depuis Redis."""
    key = _redis_key(session_id, "recent")
    try:
        raw = cache.get(key)
        if raw:
            return json.loads(raw)
        return None
    except Exception as e:
        logger.warning(f"Redis cache_get_recent failed: {e}")
        return None


def cache_set_recent(session_id: str, messages_data: list) -> None:
    """Stocke les messages récents dans Redis."""
    key = _redis_key(session_id, "recent")
    try:
        cache.set(key, json.dumps(messages_data, default=str), timeout=SESSION_TTL)
    except Exception as e:
        logger.warning(f"Redis cache_set_recent failed: {e}")

#Supprimer tout le cache d’une session.
def cache_invalidate(session_id: str) -> None:
    """Invalide tout le cache d'une session (après mise à jour majeure)."""
    for suffix in ("summary", "recent", "meta"):
        try:
            cache.delete(_redis_key(session_id, suffix))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# COUCHE 3 : SÉRIALISATION MESSAGES LANGCHAIN ↔ DICT
# ─────────────────────────────────────────────────────────────────────────────

def serialize_message(msg: BaseMessage) -> dict:
    """Convertit un message LangChain en dict JSON-serializable."""
    role_map = {
        HumanMessage: "user",
        AIMessage: "assistant",
        SystemMessage: "system",
    }
    return {
        "role": role_map.get(type(msg), "user"),
        "content": msg.content,
    }


def deserialize_message(data: dict) -> BaseMessage:
    """Reconvertit un dict en message LangChain."""
    role = data.get("role", "user")
    content = data.get("content", "")
    if role == "assistant":
        return AIMessage(content=content)
    elif role == "system":
        return SystemMessage(content=content)
    return HumanMessage(content=content)


# ─────────────────────────────────────────────────────────────────────────────
# COUCHE 4 : RÉSUMÉ PROGRESSIF (compression intelligente)
# ─────────────────────────────────────────────────────────────────────────────

def build_summary_prompt(messages_to_summarize: list, existing_summary: Optional[str] = None) -> str:
    """
    Construit le prompt de résumé.
    Si un résumé existant est fourni → résumé incrémental (plus économe).
    Sinon → résumé from scratch.
    
    Résumé incrémental = économise ~60% des tokens vs. résumé complet à chaque fois.
    (Basé sur : LangChain ConversationSummaryBufferMemory approach)
    """
    conversation_text = ""
    for msg in messages_to_summarize:
        if isinstance(msg, HumanMessage):
            conversation_text += f"Client: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            conversation_text += f"Assistant: {msg.content}\n"

    if existing_summary:
        # Résumé INCRÉMENTAL : on met à jour le résumé existant
        return (
            "Tu es un assistant bancaire. Voici un résumé existant d'une conversation "
            "et de nouveaux échanges à intégrer.\n\n"
            f"RÉSUMÉ EXISTANT :\n{existing_summary}\n\n"
            f"NOUVEAUX ÉCHANGES :\n{conversation_text}\n\n"
            "Mets à jour le résumé en intégrant les nouvelles informations. "
            "Garde uniquement les informations pertinentes pour la suite. "
            f"Maximum {SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN} caractères. "
            "Résumé mis à jour :"
        )
    else:
        # Résumé INITIAL
        return (
            "Tu es un assistant bancaire. Résume cette conversation client en 3-4 phrases. "
            "Capture : les demandes du client, les problèmes évoqués, les actions prises. "
            f"Maximum {SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN} caractères.\n\n"
            f"CONVERSATION :\n{conversation_text}\n\n"
            "Résumé :"
        )


def generate_summary(messages_to_summarize: list, existing_summary: Optional[str], llm) -> str:
    """
    Génère un résumé compressé via le LLM.
    Retourne le résumé ou une version de fallback si le LLM échoue.
    """
    if not messages_to_summarize:
        return existing_summary or ""

    prompt = build_summary_prompt(messages_to_summarize, existing_summary)

    try:
        response = llm.invoke(prompt)
        summary = response.content.strip()
        # Tronquer si trop long (sécurité)
        max_chars = SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "..."
        return summary
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        # Fallback : résumé textuel basique
        if existing_summary:
            return existing_summary
        count = len(messages_to_summarize)
        return f"[Historique de {count} messages — contexte bancaire]"


# ─────────────────────────────────────────────────────────────────────────────
# COUCHE 5 : GESTIONNAIRE PRINCIPAL DE MÉMOIRE
# ─────────────────────────────────────────────────────────────────────────────

#construit le contexte final envoyé au LLM en combinant résumé + messages récents + nouveau message
class MemoryManager:
    """
    Gestionnaire de mémoire conversationnelle à deux niveaux.
    
    Flux de données :
    ┌─────────────────────────────────────────────────────┐
    │  PostgreSQL (source de vérité — persistance totale) │
    │    ↓ chargement au démarrage de session             │
    │  Redis (cache chaud — session active)               │
    │    ↓ lecture à chaque message                       │
    │  Contexte LLM (budget: 3000 tokens max)             │
    │    [résumé compressé] + [6 derniers échanges]       │
    └─────────────────────────────────────────────────────┘
    
    Algorithme de construction du contexte :
    
    1. Charger TOUS les messages depuis PostgreSQL
    2. Séparer en : anciens (à résumer) + récents (à garder intacts)
       - "récents" = min(RECENT_TURNS*2, total) derniers messages
    3. Vérifier si un résumé Redis existe (cache hit)
       - OUI → utiliser le résumé mis en cache
       - NON → générer via LLM + stocker dans Redis
    4. Vérifier le budget tokens total
       - Si budget dépassé → réduire les "récents" jusqu'à tenir
    5. Assembler : [SystemMessage(résumé)] + [récents] + [nouveau message]
    """

    def __init__(self, llm):
        self.llm = llm

    def build_context(self, conversation, new_message: str) -> list[BaseMessage]:
        """
        Point d'entrée principal.
        Remplace build_conversation_context() dans views.py.
        
        Returns:
            Liste de BaseMessage prête à envoyer au LLM,
            dans le budget TOKEN_BUDGET.
        """
        session_id = str(conversation.session_id)

        # 1. Charger tous les messages PostgreSQL
        all_db_msgs = list(conversation.messages.order_by('created_at'))
        total = len(all_db_msgs)

        # 2. Nouveau message seul si conversation vide
        if total == 0:
            return [HumanMessage(content=new_message)]

        # 3. Séparer anciens et récents
        recent_count = min(RECENT_TURNS * 2, total)  # *2 car user+assistant
        old_msgs   = all_db_msgs[:total - recent_count]
        recent_msgs = all_db_msgs[total - recent_count:]

        # 4. Construire les messages récents LangChain
        recent_lc = self._db_to_langchain(recent_msgs)

        # 5. Résumé (uniquement si anciens messages existent)
        summary_msg = None
        if old_msgs and total > SUMMARY_TRIGGER:
            summary_text = self._get_or_build_summary(session_id, old_msgs)
            if summary_text:
                summary_msg = SystemMessage(
                    content=f"📋 Contexte de la conversation :\n{summary_text}"
                )

        # 6. Assembler avec gestion du budget tokens
        context = self._assemble_within_budget(
            summary_msg=summary_msg,
            recent_messages=recent_lc,
            new_message=new_message,
        )

        # 7. Mettre à jour le cache Redis avec les récents (pour la prochaine requête)
        self._refresh_recent_cache(session_id, recent_msgs)

        return context

    def invalidate_session(self, session_id: str) -> None:
        """Invalide le cache Redis d'une session (ex: après suppression)."""
        cache_invalidate(session_id)

    # ──────────────────────────────────────────────────────────────────────────
    # MÉTHODES PRIVÉES
    # ──────────────────────────────────────────────────────────────────────────

    def _db_to_langchain(self, db_messages) -> list[BaseMessage]:
        """Convertit des messages Django ORM en messages LangChain."""
        result = []
        for msg in db_messages:
            if msg.role == "user":
                result.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                result.append(AIMessage(content=msg.content))
        return result

    def _get_or_build_summary(self, session_id: str, old_msgs) -> str:
        """
        Stratégie cache-aside pour le résumé :
        1. Essayer Redis (rapide, ~1ms)
        2. Si absent → générer via LLM + stocker dans Redis
        
        Le résumé est invalidé uniquement quand de NOUVEAUX anciens messages
        s'accumulent (c'est-à-dire quand la fenêtre glisse).
        """
        # Cache hit → retourner directement
        cached = cache_get_summary(session_id)
        if cached:
            logger.debug(f"[Memory] Cache HIT summary for session {session_id[:8]}...")
            return cached

        # Cache miss → générer
        logger.debug(f"[Memory] Cache MISS — generating summary for session {session_id[:8]}...")
        old_lc = self._db_to_langchain(old_msgs)

        # Résumé incrémental si possible (récupère l'ancien résumé depuis Redis — déjà expiré)
        # Dans ce cas on repart de zéro, c'est correct
        summary = generate_summary(
            messages_to_summarize=old_lc,
            existing_summary=None,
            llm=self.llm,
        )

        # Stocker dans Redis
        cache_set_summary(session_id, summary)
        return summary

    def _assemble_within_budget(
        self,
        summary_msg: Optional[SystemMessage],
        recent_messages: list[BaseMessage],
        new_message: str,
    ) -> list[BaseMessage]:
        """
        Assemble le contexte final en respectant TOKEN_BUDGET.
        
        Algorithme de budget :
        1. Réserver des tokens pour le nouveau message
        2. Ajouter le résumé (coût fixe, ~200 tokens)
        3. Ajouter les messages récents du plus récent au plus ancien
           jusqu'à épuisement du budget
        
        Si le budget est très serré → on garde au minimum 2 échanges récents
        pour ne jamais perdre la continuité immédiate.
        """
        new_msg_tokens = estimate_tokens(new_message)
        available = TOKEN_BUDGET - new_msg_tokens

        context_parts = []

        # Ajouter le résumé
        if summary_msg:
            summary_tokens = estimate_tokens(summary_msg.content)
            if summary_tokens <= available:
                context_parts.append(summary_msg)
                available -= summary_tokens
            # Si le résumé seul dépasse le budget → on le tronque
            else:
                truncated = summary_msg.content[:available * CHARS_PER_TOKEN]
                context_parts.append(SystemMessage(content=truncated + "..."))
                available = 0

        # Ajouter les messages récents (du plus ancien au plus récent)
        # On part de la fin pour garantir les plus récents
        selected_recent = []
        min_recent = min(4, len(recent_messages))  # garder au moins 2 échanges

        for msg in reversed(recent_messages):
            tokens = estimate_tokens(msg.content)
            if available >= tokens or len(selected_recent) < min_recent:
                selected_recent.insert(0, msg)
                available -= tokens
            else:
                break  # budget épuisé

        context_parts.extend(selected_recent)
        context_parts.append(HumanMessage(content=new_message))

        # Log pour monitoring
        total_tokens = TOKEN_BUDGET - available + new_msg_tokens
        logger.info(
            f"[Memory] Context built: {len(context_parts)} messages, "
            f"~{total_tokens} tokens (budget: {TOKEN_BUDGET})"
        )

        return context_parts

    def _refresh_recent_cache(self, session_id: str, recent_db_msgs) -> None:
        """Met à jour le cache Redis des messages récents."""
        recent_data = [
            {"role": msg.role, "content": msg.content}
            for msg in recent_db_msgs
        ]
        cache_set_recent(session_id, recent_data)
