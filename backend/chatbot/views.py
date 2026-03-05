import uuid
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from langchain_core.messages import HumanMessage
from .graph.orchestrator import bank_graph
from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer


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

        initial_state = {
            "messages":   [HumanMessage(content=message)],
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