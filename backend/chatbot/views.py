import json
import uuid
from django.http import StreamingHttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from langchain_core.messages import HumanMessage
from .graph.orchestrator import bank_graph
from .graph.nodes import detect_intent, route_to_agent, stream_agent_response
from .graph.state import BankChatState
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer


class ChatView(APIView):
    def post(self, request):
        data       = request.data
        user_id    = data.get("user_id", "anonymous")
        session_id = data.get("session_id", str(uuid.uuid4()))
        message    = data.get("message")

        if not message:
            return Response({"error": "message requis"}, status=status.HTTP_400_BAD_REQUEST)

        # Récupérer ou créer la conversation
        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={"user_id": user_id}
        )

        # Construire l'état initial
        initial_state = {
            "messages":   [HumanMessage(content=message)],
            "user_id":    user_id,
            "session_id": session_id,
            "intent":     "",
            "agent":      "",
            "context":    {},
            "error":      None,
        }

        # Invoquer le graph
        try:
            result = bank_graph.invoke(initial_state)
            ai_response = result["messages"][-1].content
            agent_used  = result.get("agent", "unknown")

            # Sauvegarder en base
            Message.objects.create(conversation=conversation, role="user",      content=message)
            Message.objects.create(conversation=conversation, role="assistant", content=ai_response, agent_used=agent_used)

            return Response({
                "session_id": str(session_id),
                "response":   ai_response,
                "agent_used": agent_used,
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ConversationListView(APIView):
    def get(self, request):
        user_id = request.query_params.get("user_id")
        qs = Conversation.objects.all()
        if user_id:
            qs = qs.filter(user_id=user_id)
        serializer = ConversationSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ConversationDetailView(APIView):
    def get(self, request, session_id):
        try:
            conversation = Conversation.objects.get(session_id=session_id)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        messages = conversation.messages.all()
        return Response({
            "session_id":  str(conversation.session_id),
            "user_id":     conversation.user_id,
            "created_at":  conversation.created_at,
            "updated_at":  conversation.updated_at,
            "messages":    MessageSerializer(messages, many=True).data,
        }, status=status.HTTP_200_OK)

    def delete(self, request, session_id):
        try:
            conversation = Conversation.objects.get(session_id=session_id)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        conversation.delete()
        return Response({"message": "Conversation deleted"}, status=status.HTTP_204_NO_CONTENT)


class HealthCheckView(APIView):
    def get(self, request):
        try:
            from .graph.orchestrator import bank_graph  # noqa: F401
            llm_status = "ok"
        except Exception as e:
            llm_status = f"error: {str(e)}"

        return Response({
            "status":     "ok",
            "llm_status": llm_status,
            "version":    "1.0.0",
        }, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class StreamChatView(View):
    """
    Streaming endpoint — returns Server-Sent Events (SSE).
    Tokens are emitted one by one as the LLM generates them.
    """

    def post(self, request):
        try:
            data       = json.loads(request.body)
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

        # 1. Detect intent (fast, non-streaming)
        initial_state: BankChatState = {
            "messages":   [HumanMessage(content=message)],
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
        response['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
