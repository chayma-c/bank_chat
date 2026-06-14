"""
Test Suite for KB-Aware Support Agent

Tests validate:
1. In-scope queries are answered with knowledge base
2. Out-of-scope queries are rejected
3. Account-specific queries are rejected
4. No matches fall back gracefully
5. Security boundaries are enforced
"""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from chatbot.support_knowledge import (
    load_knowledge_base,
    extract_keywords,
    check_scope,
    match_faq_entry,
    build_context,
    handle_no_match,
    debug_search
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def kb():
    """Load the knowledge base once for all tests."""
    return load_knowledge_base()


@pytest.fixture
def sample_queries():
    """Common test queries."""
    return {
        # In-scope: Should be answered
        "lost_card": "My card is lost, what do I do?",
        "password_reset": "I forgot my password, how do I reset it?",
        "transfer_limit": "What's my daily transfer limit?",
        "card_declined": "Why was my card payment declined?",
        "mobile_app_crash": "The mobile app keeps crashing, help!",
        "account_opening": "How do I open a VirtuBank account?",
        "account_closure": "I want to close my account",
        "fees": "What are VirtuBank's fees?",
        "fraud_protection": "How does VirtuBank protect me from fraud?",
        "interest_rate": "What interest rate do you offer on savings?",
        "overdraft": "Does VirtuBank offer overdraft protection?",
        "card_activation": "How do I activate my new card?",
        "dispute": "How do I dispute a transaction?",
        "international_transfer": "How do I send money internationally?",
        
        # Out-of-scope: Should be rejected
        "account_balance": "What's my account balance?",
        "account_number": "What's my account number?",
        "transaction_history": "Show me my last 10 transactions",
        "personal_pin": "What's my PIN?",
        "investment_advice": "Should I invest in crypto?",
        "tax_advice": "How do I file my taxes?",
        "unrelated": "What's the weather today?",
    }


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS: Individual Functions
# ─────────────────────────────────────────────────────────────────────────────

class TestKeywordExtraction:
    """Test keyword extraction and cleaning."""
    
    def test_extract_keywords_basic(self):
        """Test basic keyword extraction."""
        result = extract_keywords("My card is lost")
        assert "card" in result
        assert "lost" in result
        assert "is" not in result  # Stop word
    
    def test_extract_keywords_punctuation(self):
        """Test handling of punctuation."""
        result = extract_keywords("My card is lost! What do I do?")
        assert "card" in result
        assert "lost" in result
        assert len(result) >= 2
    
    def test_extract_keywords_case_insensitive(self):
        """Test case insensitivity."""
        result1 = extract_keywords("LOST CARD")
        result2 = extract_keywords("lost card")
        assert set(result1) == set(result2)
    
    def test_extract_keywords_empty(self):
        """Test handling of empty/stop-word-only input."""
        result = extract_keywords("the is a")
        assert len(result) == 0
    
    def test_extract_keywords_no_single_chars(self):
        """Test filtering of single-character tokens."""
        result = extract_keywords("I a card is lost")
        assert all(len(kw) > 1 for kw in result)


class TestScopeValidation:
    """Test scope boundary enforcement."""
    
    def test_account_specific_balance(self):
        """Test rejection of balance queries."""
        is_valid, msg = check_scope("What's my account balance?")
        assert not is_valid
        assert msg  # Has rejection message
        assert "account" in msg.lower() or "personal" in msg.lower()
    
    def test_account_specific_transactions(self):
        """Test rejection of transaction queries."""
        is_valid, msg = check_scope("Show me my transactions")
        assert not is_valid
    
    def test_account_specific_account_number(self):
        """Test rejection of account number queries."""
        is_valid, msg = check_scope("What's my account number?")
        assert not is_valid
    
    def test_account_specific_pin(self):
        """Test rejection of PIN queries."""
        is_valid, msg = check_scope("What's my PIN?")
        assert not is_valid
    
    def test_out_of_scope_investment(self):
        """Test rejection of investment advice."""
        is_valid, msg = check_scope("Should I buy Bitcoin?")
        assert not is_valid
        assert msg  # Has rejection message
    
    def test_out_of_scope_tax(self):
        """Test rejection of tax questions."""
        is_valid, msg = check_scope("How do I report this for taxes?")
        assert not is_valid
    
    def test_out_of_scope_general(self):
        """Test rejection of unrelated topics."""
        is_valid, msg = check_scope("What's the weather?")
        assert not is_valid
    
    def test_in_scope_card_lost(self):
        """Test in-scope card loss query."""
        is_valid, msg = check_scope("My card is lost")
        assert is_valid
        assert msg is None
    
    def test_in_scope_password_reset(self):
        """Test in-scope password query."""
        is_valid, msg = check_scope("How do I reset my password?")
        assert is_valid
        assert msg is None
    
    def test_in_scope_transfer_limit(self):
        """Test in-scope transfer limit query."""
        is_valid, msg = check_scope("What are the transfer limits?")
        assert is_valid
        assert msg is None
    
    def test_in_scope_fees(self):
        """Test in-scope fees query."""
        is_valid, msg = check_scope("What are your fees?")
        assert is_valid
        assert msg is None


class TestFAQMatching:
    """Test FAQ retrieval and fuzzy matching."""
    
    def test_match_lost_card(self):
        """Test matching lost card query."""
        matches = match_faq_entry("My card is lost")
        assert len(matches) > 0
        assert matches[0]["id"] == "faq_lost_card"
    
    def test_match_password_reset(self):
        """Test matching password reset query."""
        matches = match_faq_entry("I forgot my password")
        assert len(matches) > 0
        assert "password" in matches[0]["id"] or "faq_password" in matches[0]["id"]
    
    def test_match_transfer_limit(self):
        """Test matching transfer limit query."""
        matches = match_faq_entry("daily transfer limit")
        assert len(matches) > 0
        assert "limit" in matches[0]["id"] or "transfer" in matches[0]["id"]
    
    def test_match_card_declined(self):
        """Test matching card decline query."""
        matches = match_faq_entry("my card was declined")
        assert len(matches) > 0
        assert "declined" in matches[0]["id"] or "card" in matches[0]["id"]
    
    def test_match_top_3_results(self):
        """Test max_results limit."""
        matches = match_faq_entry("card", max_results=3)
        assert len(matches) <= 3
    
    def test_match_similarity_threshold(self):
        """Test that low similarity matches are filtered."""
        matches = match_faq_entry("xyz qwerty asdf")
        # Should have 0 or very few matches (gibberish)
        assert len(matches) <= 1
    
    def test_match_returns_correct_fields(self):
        """Test that match dicts have required fields."""
        matches = match_faq_entry("lost card")
        assert len(matches) > 0
        
        required_fields = ["id", "category", "question", "answer", "score", "keywords_matched"]
        for match in matches:
            for field in required_fields:
                assert field in match


class TestContextBuilding:
    """Test context formatting for LLM."""
    
    def test_build_context_basic(self):
        """Test basic context building."""
        kb = load_knowledge_base()
        matches = match_faq_entry("lost card", max_results=2)
        context = build_context(matches, kb["bank_identity"])
        
        assert isinstance(context, str)
        assert len(context) > 0
        assert "VirtuBank" in context or "VIRTUBANK" in context
    
    def test_build_context_contains_answer(self):
        """Test that context includes FAQ answers."""
        kb = load_knowledge_base()
        matches = match_faq_entry("lost card", max_results=1)
        context = build_context(matches, kb["bank_identity"])
        
        # Should contain the answer text
        assert matches[0]["answer"][:50] in context or matches[0]["answer"][:30] in context
    
    def test_build_context_contains_contact(self):
        """Test that context includes bank contact info."""
        kb = load_knowledge_base()
        matches = match_faq_entry("lost card", max_results=1)
        context = build_context(matches, kb["bank_identity"])
        
        # Should include contact info
        assert "@" in context  # Email
        assert "+33" in context or "phone" in context.lower()
    
    def test_build_context_contains_constraints(self):
        """Test that context includes LLM constraint markers."""
        kb = load_knowledge_base()
        matches = match_faq_entry("lost card", max_results=1)
        context = build_context(matches, kb["bank_identity"])
        
        # Should include constraint instructions
        assert "Answer ONLY" in context or "ONLY" in context
        assert "Knowledge Base" in context


class TestFallbackMessages:
    """Test fallback message handling."""
    
    def test_fallback_no_match(self):
        """Test fallback message for no matches."""
        msg = handle_no_match("no_match")
        assert isinstance(msg, str)
        assert len(msg) > 0
        assert "support" in msg.lower()  # Should redirect to support
    
    def test_fallback_account_specific(self):
        """Test fallback for account-specific rejection."""
        msg = handle_no_match("account_specific")
        assert isinstance(msg, str)
        assert "personal" in msg.lower() or "account" in msg.lower()
    
    def test_fallback_out_of_scope(self):
        """Test fallback for out-of-scope rejection."""
        msg = handle_no_match("out_of_scope")
        assert isinstance(msg, str)
        assert "support" in msg.lower()
    
    def test_fallback_generic(self):
        """Test generic fallback."""
        msg = handle_no_match("fallback")
        assert isinstance(msg, str)
        assert "support" in msg.lower()


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS: Full Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipeline:
    """Integration tests for complete retrieval pipeline."""
    
    def test_in_scope_query_flow_lost_card(self, kb):
        """Test full pipeline: lost card query."""
        query = "My card is lost"
        
        # 1. Scope check
        is_valid, _ = check_scope(query)
        assert is_valid
        
        # 2. Keyword extraction
        keywords = extract_keywords(query)
        assert len(keywords) > 0
        
        # 3. FAQ matching
        matches = match_faq_entry(query)
        assert len(matches) > 0
        
        # 4. Context building
        context = build_context(matches, kb["bank_identity"])
        assert len(context) > 100  # Should be substantial
    
    def test_in_scope_query_flow_password(self, kb):
        """Test full pipeline: password reset query."""
        query = "How do I reset my password?"
        
        is_valid, _ = check_scope(query)
        assert is_valid
        
        keywords = extract_keywords(query)
        assert "password" in keywords or "reset" in keywords
        
        matches = match_faq_entry(query)
        assert len(matches) > 0
        assert "password" in matches[0]["id"].lower()
        
        context = build_context(matches, kb["bank_identity"])
        assert "password" in context.lower()
    
    def test_out_of_scope_query_balance(self):
        """Test out-of-scope rejection: balance."""
        query = "What's my balance?"
        
        is_valid, rejection = check_scope(query)
        assert not is_valid
        assert rejection  # Should have rejection message
        
        # Should not proceed to FAQ matching in real flow
        fallback = handle_no_match("account_specific")
        assert len(fallback) > 0
    
    def test_out_of_scope_query_investment(self):
        """Test out-of-scope rejection: investment."""
        query = "Should I invest in crypto?"
        
        is_valid, rejection = check_scope(query)
        assert not is_valid
        
        fallback = handle_no_match("out_of_scope")
        assert len(fallback) > 0
    
    def test_no_match_fallback(self):
        """Test fallback when no FAQ matches."""
        query = "xyzqwerty asdfghjk"  # Gibberish
        
        is_valid, _ = check_scope(query)
        assert is_valid  # Scope check passes
        
        matches = match_faq_entry(query)
        assert len(matches) == 0  # No matches
        
        fallback = handle_no_match("no_match")
        assert len(fallback) > 0


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY TESTS: Boundary Enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityBoundaries:
    """Test security boundary enforcement."""
    
    def test_no_access_to_balance(self):
        """Verify support agent cannot discuss balances."""
        queries = [
            "What's my balance",
            "How much money do I have",
            "Show my account balance",
            "My account balance is",
        ]
        
        for q in queries:
            is_valid, _ = check_scope(q)
            assert not is_valid, f"Should reject: {q}"
    
    def test_no_access_to_transactions(self):
        """Verify support agent cannot discuss transactions."""
        queries = [
            "Show my transactions",
            "Transaction history",
            "Last 10 transactions",
            "My recent transactions",
        ]
        
        for q in queries:
            is_valid, _ = check_scope(q)
            assert not is_valid, f"Should reject: {q}"
    
    def test_no_access_to_account_number(self):
        """Verify support agent cannot discuss account numbers."""
        queries = [
            "What's my account number",
            "Give me my IBAN",
            "My account number is",
        ]
        
        for q in queries:
            is_valid, _ = check_scope(q)
            assert not is_valid, f"Should reject: {q}"
    
    def test_no_personal_financial_advice(self):
        """Verify support agent doesn't give personal financial advice."""
        queries = [
            "Should I invest",
            "Buy crypto",
            "Tax return",
            "Mortgage help",
        ]
        
        for q in queries:
            is_valid, _ = check_scope(q)
            assert not is_valid, f"Should reject: {q}"
    
    def test_kb_constrained_responses(self):
        """Verify KB context is injected into prompts."""
        kb = load_knowledge_base()
        matches = match_faq_entry("lost card")
        context = build_context(matches, kb["bank_identity"])
        
        # Verify constraint markers are in context
        assert "ONLY" in context  # "Answer ONLY using KB"
        assert "Knowledge Base" in context


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformance:
    """Test retrieval performance."""
    
    def test_keyword_extraction_speed(self):
        """Test keyword extraction is fast."""
        import time
        
        query = "What should I do if my card is lost or stolen and I need a replacement immediately?"
        
        start = time.time()
        for _ in range(100):
            extract_keywords(query)
        elapsed = time.time() - start
        
        avg_ms = (elapsed / 100) * 1000
        assert avg_ms < 10, f"Keyword extraction too slow: {avg_ms}ms"
    
    def test_faq_matching_speed(self):
        """Test FAQ matching is fast."""
        import time
        
        query = "My card is lost"
        
        start = time.time()
        for _ in range(10):
            match_faq_entry(query)
        elapsed = time.time() - start
        
        avg_ms = (elapsed / 10) * 1000
        assert avg_ms < 50, f"FAQ matching too slow: {avg_ms}ms"
    
    def test_scope_check_speed(self):
        """Test scope check is fast."""
        import time
        
        query = "What's my account balance?"
        
        start = time.time()
        for _ in range(100):
            check_scope(query)
        elapsed = time.time() - start
        
        avg_ms = (elapsed / 100) * 1000
        assert avg_ms < 10, f"Scope check too slow: {avg_ms}ms"


# ─────────────────────────────────────────────────────────────────────────────
# DEBUG / MANUAL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDebugUtility:
    """Test the debug search utility."""
    
    def test_debug_search_returns_dict(self):
        """Test debug_search returns proper dict."""
        result = debug_search("My card is lost", verbose=False)
        
        assert isinstance(result, dict)
        assert "query" in result
        assert "keywords_extracted" in result
        assert "scope_valid" in result
        assert "matches_found" in result
        assert "matches" in result
    
    def test_debug_search_in_scope(self):
        """Test debug output for in-scope query."""
        result = debug_search("How do I reset my password?", verbose=False)
        
        assert result["scope_valid"] is True
        assert result["matches_found"] > 0
    
    def test_debug_search_out_of_scope(self):
        """Test debug output for out-of-scope query."""
        result = debug_search("What's my balance?", verbose=False)
        
        assert result["scope_valid"] is False
        assert "rejection_message" in result


# ─────────────────────────────────────────────────────────────────────────────
# Run Tests
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run with pytest
    pytest.main([__file__, "-v", "--tb=short"])
