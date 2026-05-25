import os
import sys
from unittest.mock import MagicMock, patch

# Mock dependencies before importing
sys.modules['langchain_groq'] = MagicMock()
sys.modules['langchain_ollama'] = MagicMock()
sys.modules['langchain_core.messages'] = MagicMock()
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Mock BankChatState
class BankChatState(dict):
    pass

sys.modules['.state'] = MagicMock()

# Import the code to test
# We need to set up the environment so imports work
sys.path.append(r'c:\Users\chayma\Desktop\bank_chat\backend')

with patch('chatbot.graph.nodes.llm') as mock_llm, \
     patch('chatbot.graph.nodes.httpx.post') as mock_post:

    from chatbot.graph.nodes import stream_agent_response

    # Test Case 1: TALK decision
    mock_llm.invoke.return_value.content = "REASONING: C'est une question générale sur la fraude.\nDECISION: TALK"
    mock_llm.stream.return_value = [MagicMock(content="Ceci "), MagicMock(content="est "), MagicMock(content="un conseil.")]
    
    messages = [HumanMessage(content="Qu'est-ce que le phishing ?")]
    
    print("--- Test Case 1: TALK ---")
    results = list(stream_agent_response("fraud", messages))
    for token, agent, *extra in results:
        print(f"[{agent}] {token}")

    # Test Case 2: ANALYZE decision
    mock_llm.invoke.return_value.content = "REASONING: L'utilisateur a fourni un IBAN.\nDECISION: ANALYZE"
    mock_post.return_value.json.return_value = {"llm_summary": "L'IBAN semble légitime."}
    mock_post.return_value.raise_for_status = MagicMock()

    messages = [HumanMessage(content="Vérifie cet IBAN: FR7612345")]
    
    print("\n--- Test Case 2: ANALYZE ---")
    results = list(stream_agent_response("fraud", messages))
    for token, agent, *extra in results:
        print(f"[{agent}] {token}")
