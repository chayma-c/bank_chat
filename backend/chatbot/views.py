import json
import uuid
import logging
import httpx
import os
import jwt
from django.http import StreamingHttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from .graph.orchestrator import bank_graph
from .graph.nodes import detect_intent, stream_agent_response, llm, mail_agent
from .graph.state import BankChatState
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer
from .memory_manager import MemoryManager
from .auth.authentication import KeycloakAuthentication
from .auth.permissions import IsAuthenticated, IsBankAgent, IsAdmin
from .auth.keycloak_client import list_realm_users, get_user_role_mappings, update_user_role
from django.conf import settings

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")

# ── Singleton mémoire ─────────────────────────────────────────────────────────
memory_manager = MemoryManager(llm=llm)

# ── Constants for role-gated agents ───────────────────────────────────────────
_RESTRICTED_AGENTS = frozenset({'fraud', 'sql'})
_BANK_AGENT_ROLES  = frozenset({'bank_agent', 'admin'})


def _get_realm_roles(request) -> frozenset:
    """
    Extract realm roles from the Bearer JWT for non-DRF views.
    Returns an empty frozenset if the token is absent, malformed, or expired.
    """
    from .auth.keycloak_client import get_public_key
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return frozenset()
    token = header[7:]
    try:
        public_key = get_public_key()
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.KEYCLOAK_CLIENT_ID,
            issuer=f"{settings.KEYCLOAK_ISSUER}/realms/{settings.KEYCLOAK_REALM}",
            options={"verify_exp": True},
        )
        return frozenset(payload.get("realm_access", {}).get("roles", []))
    except jwt.ExpiredSignatureError:
        logger.warning("[_get_realm_roles] JWT token expired")
        return frozenset()
    except jwt.InvalidTokenError as e:
        logger.warning("[_get_realm_roles] Invalid JWT token: %s", e)
        return frozenset()
    except Exception as e:
        logger.exception("[_get_realm_roles] Unexpected error during role extraction: %s", e)
        return frozenset()


# ── Vues ──────────────────────────────────────────────────────────────────────

class ChatView(APIView):
    """Mode non-streaming — passe par le graph LangGraph complet (mail_agent inclus)."""
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAuthenticated]

    def post(self, request):
        data           = request.data
        user_id        = data.get("user_id", "anonymous")
        session_id     = data.get("session_id", str(uuid.uuid4()))
        message        = data.get("message")
        selected_agent = data.get("selected_agent", None)

        # ── Role check: fraud & SQL agents require bank_agent or admin ──────
        if selected_agent in _RESTRICTED_AGENTS:
            user_roles = (
                set(request.user.get('roles', []))
                if isinstance(request.user, dict) else set()
            )
            if not (user_roles & _BANK_AGENT_ROLES):
                return Response(
                    {"error": "Accès refusé : rôle bank_agent ou admin requis pour cet agent."},
                    status=403,
                )

        if not message:
            return Response({"error": "message requis"}, status=400)

        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        conversation_messages = memory_manager.build_context(conversation, message)

        auth_header = request.headers.get("Authorization")
        auth_token = auth_header.split(" ")[1] if auth_header and "Bearer " in auth_header else None

        initial_state = {
            "messages":       conversation_messages,
            "user_id":        user_id,
            "session_id":     session_id,
            "auth_token":     auth_token,
            "intent":         "",
            "agent":          "",
            "selected_agent": selected_agent,
            "context":        {},
            "error":          None,
        }

        try:
            result      = bank_graph.invoke(initial_state)
            ai_response = result["messages"][-1].content
            agent_used  = result.get("agent", "unknown")

            Message.objects.create(conversation=conversation, role="user",      content=message)
            Message.objects.create(conversation=conversation, role="assistant", content=ai_response, agent_used=agent_used)

            return Response({
                "session_id": str(session_id),
                "response":   ai_response,
                "agent_used": agent_used,
            })

        except Exception as e:
            logger.exception("ChatView error")
            return Response({"error": str(e)}, status=500)


class ConversationListView(APIView):
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAuthenticated]

    def get(self, request):
        # By default, only show conversations for the authenticated user
        user_id = request.query_params.get("user_id") or request.user.get("user_id")
        
        qs = Conversation.objects.all()
        if user_id:
            qs = qs.filter(user_id=user_id)
        return Response(ConversationSerializer(qs, many=True).data)


class ConversationDetailView(APIView):
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAuthenticated]

    def get(self, request, session_id):
        try:
            conv = Conversation.objects.get(session_id=session_id)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=404)

        return Response({
            "session_id": str(conv.session_id),
            "user_id":    conv.user_id,
            "created_at": conv.created_at,
            "messages":   MessageSerializer(conv.messages.all(), many=True).data,
        })

    def delete(self, request, session_id):
        try:
            conv = Conversation.objects.get(session_id=session_id)
            memory_manager.invalidate_session(str(conv.session_id))
            conv.delete()
        except Conversation.DoesNotExist:
            return Response({"error": "Not found"}, status=404)
        return Response(status=204)


class HealthCheckView(APIView):
    def get(self, request):
        return Response({"status": "ok", "version": "1.0.0"})


@method_decorator(csrf_exempt, name='dispatch')
class StreamChatView(View):
    """
    Mode streaming — génère les tokens en SSE.

    CORRECTION : après une analyse fraude (path ANALYZE), appelle mail_agent
    directement depuis generate() car stream_agent_response() bypass le graph.
    """
    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return StreamingHttpResponse(
                iter([f'data: {json.dumps({"error": "Invalid JSON"})}\n\n']),
                content_type='text/event-stream', status=400
            )

        user_id        = data.get("user_id", "anonymous")
        session_id     = data.get("session_id", str(uuid.uuid4()))
        message        = data.get("message", "").strip()
        selected_agent = data.get("selected_agent", None)

        # ── Role check: fraud & SQL agents require bank_agent or admin ──────
        if selected_agent in _RESTRICTED_AGENTS:
            user_roles = _get_realm_roles(request)
            if not (user_roles & _BANK_AGENT_ROLES):
                def _forbidden():
                    yield f'data: {json.dumps({"error": "Accès refusé : rôle bank_agent ou admin requis pour cet agent."})}\n\n'
                return StreamingHttpResponse(
                    _forbidden(), content_type='text/event-stream', status=403
                )

        if not message:
            return StreamingHttpResponse(
                iter([f'data: {json.dumps({"error": "message requis"})}\n\n']),
                content_type='text/event-stream', status=400
            )

        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        conversation_messages = memory_manager.build_context(conversation, message)

        auth_header = request.headers.get("Authorization")
        auth_token = auth_header.split(" ")[1] if auth_header and "Bearer " in auth_header else None

        initial_state: BankChatState = {
            "messages":       conversation_messages,
            "user_id":        user_id,
            "session_id":     session_id,
            "auth_token":     auth_token,
            "intent":         "",
            "agent":          "",
            "selected_agent": selected_agent,
            "context":        {},
            "error":          None,
        }

        # detect_intent retourne un state enrichi avec intent + messages
        intent_state = detect_intent(initial_state)
        intent       = intent_state["intent"]

        def generate():
            full_response = ""
            agent_used    = "fallback"
            fraud_result  = None

            try:
                for token, agent_key, *extra in stream_agent_response(intent, intent_state):
                    full_response += token
                    agent_used     = agent_key
                    yield f'data: {json.dumps({"token": token, "agent": agent_key})}\n\n'

                    # stream_agent_response peut yielder un 3e élément : le résultat fraude brut
                    if extra and isinstance(extra[0], dict):
                        fraud_result = extra[0]

                # ── Sauvegarder les messages ───────────────────────────────────
                Message.objects.create(conversation=conversation, role="user",      content=message)
                Message.objects.create(conversation=conversation, role="assistant", content=full_response, agent_used=agent_used)

                # ── Déclencher mail_agent si analyse fraude effectuée ──────────
                # C'est ici que le mail est envoyé en mode streaming,
                # car stream_agent_response() ne passe PAS par le graph LangGraph.
                if agent_used == "fraud_agent" and fraud_result and fraud_result.get("iban"):
                    try:
                        mail_state: BankChatState = {
                            **intent_state,
                            "context": fraud_result,
                            "agent":   "fraud_agent",
                        }
                        mail_agent(mail_state)
                        logger.info(f"[StreamChatView] mail_agent called for IBAN={fraud_result.get('iban')}")
                    except Exception as mail_err:
                        logger.warning(f"[StreamChatView] mail_agent failed (non-blocking): {mail_err}")

                yield f'data: {json.dumps({"done": True, "session_id": str(session_id), "agent": agent_used})}\n\n'

            except Exception as e:
                logger.exception("StreamChatView generate() error")
                yield f'data: {json.dumps({"error": str(e)})}\n\n'

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control']               = 'no-cache'
        response['X-Accel-Buffering']           = 'no'
        response['Access-Control-Allow-Origin'] = 'http://localhost:4200'
        return response

    def options(self, request):
        response = StreamingHttpResponse(iter([]), content_type='text/event-stream')
        response['Access-Control-Allow-Origin']  = 'http://localhost:4200'
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response


class FraudAnalyzeView(APIView):
    """Direct fraud analysis endpoint — délègue au fraud-service via HTTP."""
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAuthenticated, IsBankAgent]

    def post(self, request):
        data       = request.data
        iban       = data.get("iban", "")
        action     = data.get("action", "fraud_check")
        user_id    = data.get("user_id", "anonymous")
        session_id = data.get("session_id", str(uuid.uuid4()))
        excel_path = data.get("excel_path", "")

        if not iban:
            return Response(
                {"error": "IBAN requis. Fournissez un IBAN valide."},
                status=400,
            )

        try:
            # Note: call_fraud_service logic should ideally be here or in a shared helper.
            # Assuming nodes.py _get_fraud_decision_and_result can be adapted or this uses direct httpx
            # For simplicity, keeping the logic direct here if it was removed from helpers
            headers = {"Authorization": request.headers.get("Authorization")}
            resp = httpx.post(
                f"{FRAUD_SERVICE_URL}/analyze",
                json={
                    "iban":       iban,
                    "action":     action,
                    "user_id":    user_id,
                    "session_id": session_id,
                    "excel_path": excel_path,
                },
                headers=headers,
                timeout=120.0
            )
            resp.raise_for_status()
            return Response(resp.json())

        except httpx.TimeoutException:
            return Response(
                {"error": "Le service d'analyse de fraude ne répond pas (timeout 120s)."},
                status=504,
            )
        except httpx.HTTPStatusError as e:
            return Response(
                {"error": f"Erreur du service de fraude : HTTP {e.response.status_code}"},
                status=502,
            )
        except Exception as e:
            logger.exception("FraudAnalyzeView error")
            return Response({"error": str(e)}, status=500)


# ── User Management (Admin Only) ──────────────────────────────────────────────

class UserListView(APIView):
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAdmin]

    def get(self, request):
        try:
            users = list_realm_users()
            enriched_users = []
            for u in users:
                roles = get_user_role_mappings(u["id"])
                u["roles"] = [r["name"] for r in roles]
                enriched_users.append(u)
            return Response(enriched_users)
        except Exception as e:
            logger.exception("UserListView error")
            return Response({"error": str(e)}, status=503)

class UserRoleUpdateView(APIView):
    authentication_classes = [KeycloakAuthentication]
    permission_classes     = [IsAdmin]

    def post(self, request):
        user_id   = request.data.get("user_id")
        role_name = request.data.get("role")
        action    = request.data.get("action", "add") # 'add' or 'remove'
        
        if not all([user_id, role_name]):
            return Response({"error": "user_id and role are required"}, status=400)
        
        if role_name not in ["bank_agent", "admin"]:
             return Response({"error": "Only 'bank_agent' and 'admin' roles can be managed."}, status=400)

        try:
            update_user_role(user_id, role_name, action)
            return Response({"status": "success", "message": f"Role {role_name} {action}ed successfully."})
        except Exception as e:
            logger.exception("UserRoleUpdateView error")
            return Response({"error": str(e)}, status=503)