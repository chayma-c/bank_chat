import sys
import os
import io
from unittest.mock import MagicMock

# Fix encoding for Windows shell
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root to sys.path
sys.path.append(r"c:\Users\chayma\Desktop\bank_chat\backend")

# Mock the LLM to avoid external calls
mock_llm = MagicMock()
import chatbot.graph.nodes as nodes
nodes.llm = mock_llm

from chatbot.graph.nodes import detect_intent
from langchain_core.messages import HumanMessage

def test_select_agent_override():
    print("Running test_select_agent_override...")
    
    # Test 1: Selected agent is "fraud"
    state1 = {
        "messages": [HumanMessage(content="Hello")],
        "selected_agent": "fraud",
        "intent": ""
    }
    result1 = detect_intent(state1)
    assert result1["intent"] == "fraud"
    print("✅ Test 1 Passed: Forced fraud agent")

    # Test 2: Selected agent is "orchestrator" (should use classification)
    state2 = {
        "messages": [HumanMessage(content="I want to see my balance")],
        "selected_agent": "orchestrator",
        "intent": ""
    }
    mock_llm.invoke.return_value.content = "account"
    result2 = detect_intent(state2)
    assert result2["intent"] == "account"
    print("✅ Test 2 Passed: Orchestrator mode uses classification")

    # Test 3: Selected agent is "text_to_sql"
    state3 = {
        "messages": [HumanMessage(content="how many users?")],
        "selected_agent": "text_to_sql",
        "intent": ""
    }
    result3 = detect_intent(state3)
    assert result3["intent"] == "text_to_sql"
    print("✅ Test 3 Passed: Forced text_to_sql agent")

    # Test 4: Selected agent is None (should use classification)
    state4 = {
        "messages": [HumanMessage(content="Help me")],
        "selected_agent": None,
        "intent": ""
    }
    mock_llm.invoke.return_value.content = "support"
    result4 = detect_intent(state4)
    assert result4["intent"] == "support"
    print("✅ Test 4 Passed: None selection uses classification")

if __name__ == "__main__":
    try:
        test_select_agent_override()
        print("\nAll Select Agent tests passed successfully!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
    except Exception as e:
        print(f"\n❌ An error occurred during testing: {e}")

