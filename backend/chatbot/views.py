import json
import uuid
from django.http import StreamingHttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from .graph.orchestrator import bank_graph
from .graph.nodes import detect_intent, stream_agent_response, llm
from .graph.state import BankChatState
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer
from .graph.fraud.graph import run_fraud_agent


# ── Fonction helper pour construire le contexte avec résumé adaptatif ────────

def build_conversation_context(conversation: Conversation, new_message: str) -> list:
    """
    Construit le contexte de la conversation avec résumé adaptatif intelligent.
    
    Stratégie :
    - Si <= 20 messages : garde tout l'historique
    - Si > 20 messages :
        * Résume les anciens messages via LLM
        * Garde les 5 derniers échanges (10 messages) en détail
    
    Args:
        conversation: L'objet Conversation Django
        new_message: Le nouveau message de l'utilisateur
        
    Returns:
        Liste de messages LangChain (HumanMessage, AIMessage, SystemMessage)
    """
    all_messages = list(conversation.messages.order_by('created_at'))
    total_count = len(all_messages)
    
    history = []
    
    if total_count > 20:
        # STRATÉGIE AVANCÉE : Résumé + contexte récent
        
        # 1. Séparer anciens et récents (garde les 10 derniers = 5 échanges)
        old_messages = all_messages[:total_count - 10]
        recent_messages = all_messages[total_count - 10:]
        
        # 2. Générer résumé intelligent via LLM
        conversation_text = ""
        for msg in old_messages:
            if msg.role == "user":
                conversation_text += f"Client: {msg.content}\n"
            elif msg.role == "assistant":
                conversation_text += f"Assistant: {msg.content}\n"
        
        summary_prompt = (
            "Tu es un assistant bancaire. Voici l'historique d'une conversation avec un client. "
            "Crée un résumé contextualisé et concis (3-5 phrases) qui capture :\n"
            "- Les informations clés mentionnées par le client (nom, problèmes, demandes)\n"
            "- Les actions ou réponses importantes données par l'assistant\n"
            "- Le contexte général nécessaire pour continuer la conversation\n\n"
            f"Historique :\n{conversation_text}\n\n"
            "Résumé contextuel :"
        )
        
        try:
            summary_response = llm.invoke(summary_prompt)
            summary_text = summary_response.content
            
            # Ajouter le résumé comme message système
            history.append(SystemMessage(content=f"📋 Contexte de la conversation précédente :\n{summary_text}"))
        except Exception as e:
            # Fallback : résumé simple si le LLM échoue
            summary_text = f"Résumé : Conversation de {len(old_messages)} messages précédents."
            history.append(SystemMessage(content=summary_text))
        
        # 3. Ajouter les 5 derniers échanges en détail
        for msg in recent_messages:
            if msg.role == "user":
                history.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                history.append(AIMessage(content=msg.content))
    
    else:
        # STRATÉGIE SIMPLE : Garde tout l'historique (< 20 messages)
        for msg in all_messages:
            if msg.role == "user":
                history.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                history.append(AIMessage(content=msg.content))
    
    # Ajouter le nouveau message utilisateur
    history.append(HumanMessage(content=new_message))
    
    return history


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

        # Construire le contexte avec résumé adaptatif intelligent
        conversation_messages = build_conversation_context(conversation, message)

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
            Conversation.objects.get(session_id=session_id).delete()
        except Conversation.DoesNotExist:
            return Response({"error": "Not found"}, status=404)
        return Response(status=204)


class HealthCheckView(APIView):
    def get(self, request):
        return Response({"status": "ok", "version": "1.0.0"})


@method_decorator(csrf_exempt, name='dispatch')
class StreamChatView(View):
    """
    Streaming endpoint — returns Server-Sent Events (SSE).
    Tokens are emitted one by one as the LLM generates them.
    """

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

        # Get or create conversation
        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        # Construire le contexte avec résumé adaptatif intelligent
        conversation_messages = build_conversation_context(conversation, message)

        # 1. Detect intent (fast, non-streaming)
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

        # 2. Stream the specialized agent's response token by token
        def generate():
            full_response = ""
            agent_used    = "fallback"

            try:
                for token, agent_key in stream_agent_response(intent, intent_state["messages"]):
                    full_response += token
                    agent_used     = agent_key
                    yield f'data: {json.dumps({"token": token, "agent": agent_key})}\n\n'

                # Save both messages once full response is assembled
                Message.objects.create(conversation=conversation, role="user",      content=message)
                Message.objects.create(conversation=conversation, role="assistant", content=full_response, agent_used=agent_used)

                yield f'data: {json.dumps({"done": True, "session_id": str(session_id), "agent": agent_used})}\n\n'

            except Exception as e:
                yield f'data: {json.dumps({"error": str(e)})}\n\n'

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control']    = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        response['Access-Control-Allow-Origin'] = 'http://localhost:4200'
        return response

    def options(self, request):
        response = StreamingHttpResponse(iter([]), content_type='text/event-stream')
        response['Access-Control-Allow-Origin']  = 'http://localhost:4200'
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
# ══════════════════════════════════════════════════════════════════════

class FraudAnalyzeView(APIView):
    """
    Direct fraud analysis endpoint.
    
    POST /api/fraud/analyze/
    {
        "iban": "IBAN_FR123",
        "action": "fraud_check",      // or "export_transactions"
        "user_id": "user123",         // optional
        "session_id": "xxx",          // optional
        "excel_path": ""              // optional, defaults to data/transactions.xlsx
    }
    """

    def post(self, request):
        data = request.data
        iban = data.get("iban", "")
        action = data.get("action", "fraud_check")
        user_id = data.get("user_id", "anonymous")
        session_id = data.get("session_id", str(uuid.uuid4()))
        excel_path = data.get("excel_path", "")

        if not iban:
            return Response(
                {"error": "IBAN requis. Fournissez un IBAN valide."},
                status=400,
            )

        # Build a synthetic message for the fraud agent
        if action == "export_transactions":
            user_msg = f"Exporte toutes les transactions pour l'IBAN {iban}"
        else:
            user_msg = f"Analyse les fraudes pour l'IBAN {iban}"

        messages = [HumanMessage(content=user_msg)]

        try:
            result = run_fraud_agent(
                messages=messages,
                user_id=user_id,
                session_id=session_id,
                excel_path=excel_path,
            )

            return Response({
                "iban": result.get("iban", iban),
                "action": result.get("action", action),
                "transactions_count": result.get("transactions_count", 0),
                "account_summary": result.get("account_summary"),
                "score_behavioral": result.get("score_behavioral", 0),
                "score_aml": result.get("score_aml", 0),
                "score_final": result.get("score_final", 0),
                "risk_level": result.get("risk_level", ""),
                "tracfin_required": result.get("tracfin_required", False),
                "fraud_results": result.get("fraud_results", []),
                "report_path": result.get("report_path", ""),
                "summary": result.get("llm_summary", ""),
                "error": result.get("error"),
            })

        except Exception as e:
            return Response({"error": str(e)}, status=500)