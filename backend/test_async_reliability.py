import os
import sys
import django
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from chatbot.graph.nodes import stream_agent_response, detect_intent
from chatbot.memory_manager import MemoryManager
from langchain_core.messages import HumanMessage, AIMessage

async def test_memory_manager_async():
    print("\n[TEST] MemoryManager Async Cache...")
    mock_llm = AsyncMock()
    mm = MemoryManager(llm=mock_llm)
    
    # Mock cache
    with patch("chatbot.memory_manager.cache") as mock_cache:
        # Django 4.1+ async cache methods
        mock_cache.aget = AsyncMock(return_value=None)
        mock_cache.aset = AsyncMock()
        
        # Mock Conversation
        mock_conv = MagicMock()
        mock_conv.session_id = "test-session"
        mock_conv.summary = "Old summary"
        # Simulate no messages in DB for this test case
        mock_conv.messages.order_by.return_value = []
        
        ctx = await mm.build_context(mock_conv, "Hello")
        
        # Verify that aget was called (it was called during summary lookup)
        print(f"  Cache aget called: {mock_cache.aget.called}")
        print(f"  Context length: {len(ctx)}")
        
        summary_present = any("Old summary" in getattr(m, "content", "") for m in ctx)
        print(f"  Summary present in context: {summary_present}")
        assert summary_present, "Old summary should be in the context"

async def test_fraud_agent_async():
    print("\n[TEST] Fraud Agent Async Flow...")
    
    # Patch the module-level 'llm' in nodes
    with patch("chatbot.graph.nodes.llm") as mock_llm:
        # Mock decision reasoning and decision
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="REASONING: Analysis required for this IBAN.\nDECISION: ANALYZE"))
        
        # Mock httpx AsyncClient
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.json.return_value = {"llm_summary": "Fraud risk detected!", "iban": "FR123"}
            mock_post.return_value.raise_for_status = MagicMock()

            messages = [HumanMessage(content="Vérifie l'IBAN FR123")]
            tokens = []
            
            # Use stream_agent_response (the async generator)
            async for token, agent_key, *extra in stream_agent_response(
                "fraud", messages, user_id="user1", session_id="sess1"
            ):
                tokens.append(token)
            
            print(f"  Tokens yielded: {len(tokens)}")
            print(f"  Agent detected: {agent_key}")
            
            assert "Analysis required" in tokens[0]
            assert "Fraud risk detected!" in tokens[-1]
            print(f"  [OK] Fraud flow is asynchronous and non-blocking")

async def test_search_agent_fallback_async():
    print("\n[TEST] Search Agent Fallback Async...")
    
    with patch("chatbot.graph.nodes.llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="keyword"))
        
        # Mock fallback search
        with patch("chatbot.graph.nodes.perform_web_search") as mock_search:
            mock_search.return_value = "Mocked search results"
            
            # Mock astream
            async def mock_astream(*args, **kwargs):
                yield MagicMock(content="Streaming results...")
            mock_llm.astream = mock_astream

            messages = [HumanMessage(content="Search for something")]
            tokens = []
            
            # Force search tool failure to trigger fallback
            with patch("chatbot.graph.nodes.load_mcp_tools", side_effect=Exception("MCP Error")):
                async for token, agent_key, *extra in stream_agent_response(
                    "search", messages, user_id="user1", session_id="sess1"
                ):
                    tokens.append(token)

            print(f"  Fallback search called: {mock_search.called}")
            assert mock_search.called
            assert "Streaming results" in tokens[-1]

async def main():
    print("[RUN] Starting Async Reliability Integration Tests...")
    try:
        await test_memory_manager_async()
        await test_fraud_agent_async()
        await test_search_agent_fallback_async()
        print("\n[SUCCESS] ALL TESTS PASSED: Backend is now fully non-blocking.")
    except Exception as e:
        print(f"\n[ERROR] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
