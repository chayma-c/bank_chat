import json
import uuid
import logging
import httpx
import os
from django.http import StreamingHttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from .graph.orchestrator import bank_graph
from .graph.nodes import detect_intent, stream_agent_response, llm
from .graph.state import BankChatState
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer
from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")

# ── Singleton mémoire ─────────────────────────────────────────────────────────
memory_manager = MemoryManager(llm=llm)


# ── Helper : appel HTTP au fraud-service ──────────────────────────────────────

def call_fraud_service(iban: str, action: str, user_id: str,
                       session_id: str, excel_path: str) -> dict:
    """
    Envoie une requête au fraud-service microservice.
    Lève une exception si le service est injoignable ou renvoie une erreur.
    """
    response = httpx.post(
        f"{FRAUD_SERVICE_URL}/analyze",
        json={
            "iban":       iban,
            "action":     action,
            "user_id":    user_id,
            "session_id": session_id,
            "excel_path": excel_path,
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()


# ── Vues ──────────────────────────────────────────────────────────────────────

class ChatView(APIView):
    def post(self, request):
        data       = request.data
        user_id    = data.get("user_id", "anonymous")
        session_id = data.get("session_id", str(uuid.uuid4()))
        message    = data.get("message")

        if not message:
            return Response({"error": "message requis"}, status=400)

        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        conversation_messages = memory_manager.build_context(conversation, message)

        initial_state = {
            "messages":   conversation_messages,
            "user_id":    user_id,
            "session_id": session_id,
            "intent":     "",
            "agent":      "",
            "context":    {},
            "error":      None,
        }

        try:
            # Appel au graph pour obtenir la réponse de l'IA
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
    def get(self, request):
        user_id = request.query_params.get("user_id")
        qs = Conversation.objects.all()
        if user_id:
            qs = qs.filter(user_id=user_id)
        return Response(ConversationSerializer(qs, many=True).data)


class ConversationDetailView(APIView):
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
    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return StreamingHttpResponse(
                iter([f'data: {json.dumps({"error": "Invalid JSON"})}\n\n']),
                content_type='text/event-stream', status=400
            )

        user_id    = data.get("user_id", "anonymous")
        session_id = data.get("session_id", str(uuid.uuid4()))
        message    = data.get("message", "").strip()

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

        initial_state: BankChatState = {
            "messages":   conversation_messages,
            "user_id":    user_id,
            "session_id": session_id,
            "intent":     "",
            "agent":      "",
            "context":    {},
            "error":      None,
        }
        intent_state = detect_intent(initial_state)
        intent       = intent_state["intent"]

        def generate():
            full_response = ""
            agent_used    = "fallback"
            try:
                for token, agent_key in stream_agent_response(intent, intent_state["messages"]):
                    full_response += token
                    agent_used     = agent_key
                    yield f'data: {json.dumps({"token": token, "agent": agent_key})}\n\n'

                Message.objects.create(conversation=conversation, role="user",      content=message)
                Message.objects.create(conversation=conversation, role="assistant", content=full_response, agent_used=agent_used)

                yield f'data: {json.dumps({"done": True, "session_id": str(session_id), "agent": agent_used})}\n\n'

            except Exception as e:
                logger.exception("StreamChatView generate() error")
                yield f'data: {json.dumps({"error": str(e)})}\n\n'

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control']             = 'no-cache'
        response['X-Accel-Buffering']          = 'no'
        response['Access-Control-Allow-Origin'] = 'http://localhost:4200'
        return response

    def options(self, request):
        response = StreamingHttpResponse(iter([]), content_type='text/event-stream')
        response['Access-Control-Allow-Origin']  = 'http://localhost:4200'
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response


class FraudAnalyzeView(APIView):
    """
    Direct fraud analysis endpoint — délègue au fraud-service via HTTP.

    POST /api/v1/chatbot/fraud/analyze/
    {
        "iban": "IBAN_FR123",
        "action": "fraud_check",
        "user_id": "user123",
        "session_id": "xxx",
        "excel_path": ""
    }
    """

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
            result = call_fraud_service(
                iban=iban,
                action=action,
                user_id=user_id,
                session_id=session_id,
                excel_path=excel_path,
            )
            return Response(result)

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