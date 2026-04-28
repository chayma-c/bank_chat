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
# ── CORRECTION 1 : mail_agent était absent de cet import ────────────────────
from .graph.nodes import detect_intent, stream_agent_response, llm, mail_agent
from .graph.state import BankChatState
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer
from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")

# ── Singleton mémoire ─────────────────────────────────────────────────────────
memory_manager = MemoryManager(llm=llm)


def call_fraud_service(iban: str, action: str, user_id: str,
                       session_id: str, excel_path: str) -> dict:
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


class ChatView(APIView):
    def post(self, request):
        data           = request.data
        user_id        = data.get("user_id", "anonymous")
        session_id     = data.get("session_id", str(uuid.uuid4()))
        message        = data.get("message")
        selected_agent = data.get("selected_agent", None)

        if not message:
            return Response({"error": "message requis"}, status=400)

        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        conversation_messages = memory_manager.build_context(conversation, message)

        initial_state = {
            "messages":       conversation_messages,
            "user_id":        user_id,
            "session_id":     session_id,
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
    """
    Mode streaming SSE.

    CORRECTIONS :
      1. mail_agent importé (manquait)
      2. stream_agent_response reçoit user_id + session_id
      3. Déstructuration *extra pour capturer fraud_result (3e élément)
      4. mail_agent appelé après streaming si fraude ANALYZE détectée
      5. selected_agent transmis dans le state initial
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
        selected_agent = data.get("selected_agent", data.get("agent", None))

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
            "messages":       conversation_messages,
            "user_id":        user_id,
            "session_id":     session_id,
            "intent":         "",
            "agent":          "",
            "selected_agent": selected_agent,
            "context":        {},
            "error":          None,
        }

        intent_state = detect_intent(initial_state)
        intent       = intent_state["intent"]

        def generate():
            full_response = ""
            agent_used    = "fallback"
            fraud_result  = None   # rempli si ANALYZE path fraude

            try:
                # ── CORRECTIONS 2 + 3 ────────────────────────────────────────
                # - user_id et session_id transmis à stream_agent_response
                # - *extra capturele 3e élément yielded : le dict /analyze
                for token, agent_key, *extra in stream_agent_response(
                    intent,
                    intent_state["messages"],
                    user_id=user_id,
                    session_id=session_id,
                ):
                    full_response += token
                    agent_used     = agent_key
                    yield f'data: {json.dumps({"token": token, "agent": agent_key})}\n\n'

                    # Capturer le résultat fraude brut (3e élément du yield)
                    if extra and isinstance(extra[0], dict) and extra[0].get("iban"):
                        fraud_result = extra[0]
                        logger.info(
                            f"[StreamChatView] fraud_result captured — "
                            f"IBAN={fraud_result.get('iban')} "
                            f"score={fraud_result.get('score_final')} "
                            f"decision_log_id={fraud_result.get('decision_log_id', 'MISSING')!r}"
                        )

                # ── Sauvegarder en BD ─────────────────────────────────────────
                Message.objects.create(
                    conversation=conversation, role="user", content=message
                )
                Message.objects.create(
                    conversation=conversation, role="assistant",
                    content=full_response, agent_used=agent_used
                )

                # ── CORRECTION 4 : appeler mail_agent ────────────────────────
                # Le streaming bypass le graph LangGraph entier.
                # mail_agent doit être appelé manuellement ici avec le
                # fraud_result (qui contient decision_log_id + scores + IBAN).
                if agent_used == "fraud_agent" and fraud_result and fraud_result.get("iban"):
                    try:
                        logger.info(
                            f"[StreamChatView] Triggering mail_agent — "
                            f"IBAN={fraud_result.get('iban')} "
                            f"score={fraud_result.get('score_final')}"
                        )
                        mail_state: BankChatState = {
                            **intent_state,
                            "context":    fraud_result,
                            "agent":      "fraud_agent",
                            "user_id":    user_id,
                            "session_id": session_id,
                        }
                        mail_agent(mail_state)
                        logger.info("[StreamChatView] ✅ mail_agent completed")
                    except Exception as mail_err:
                        logger.warning(
                            f"[StreamChatView] mail_agent failed (non-blocking): {mail_err}"
                        )
                elif agent_used == "fraud_agent":
                    logger.info(
                        "[StreamChatView] fraud_agent TALK path — mail_agent not called"
                    )

                yield f'data: {json.dumps({"done": True, "session_id": str(session_id), "agent": agent_used})}\n\n'

            except Exception as e:
                logger.exception("StreamChatView generate() error")
                yield f'data: {json.dumps({"error": str(e)})}\n\n'

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control']              = 'no-cache'
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
    """Direct fraud analysis endpoint — délègue au fraud-service via HTTP."""

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
                iban=iban, action=action, user_id=user_id,
                session_id=session_id, excel_path=excel_path,
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