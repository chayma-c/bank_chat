"""
Support Knowledge Base Module

Provides keyword-based FAQ retrieval with fuzzy matching for the support agent.
Uses fuzzywuzzy for Levenshtein distance-based similarity scoring.

Functions:
- load_knowledge_base(): Load KB from JSON
- extract_keywords(): Extract and clean keywords from user input
- match_faq_entry(): Fuzzy match keywords to FAQ entries
- check_scope(): Validate if query is in-scope for support agent
- build_context(): Format matched FAQ entries for LLM context
- handle_no_match(): Return appropriate fallback message
"""

import json
import os
import re
from typing import Dict, List, Tuple, Optional
from pathlib import Path

# Import fuzzywuzzy for fuzzy string matching
try:
    from fuzzywuzzy import fuzz
except ImportError:
    raise ImportError("fuzzywuzzy not installed. Install with: pip install fuzzywuzzy python-Levenshtein")


# ─────────────────────────────────────────────────────────────────────────────
# Global Cache: Load knowledge base once and reuse
# ─────────────────────────────────────────────────────────────────────────────

_KB_CACHE = None


def load_knowledge_base() -> Dict:
    """
    Load the support knowledge base from JSON file.
    
    Results are cached in memory to avoid repeated disk reads.
    First call loads from disk; subsequent calls return cached copy.
    
    Returns:
        Dict: Complete knowledge base with structure:
            - meta: Version and metadata
            - bank_identity: VirtuBank details
            - products: List of financial products
            - faq: List of FAQ entries with keywords
            - scope: in_scope and out_of_scope topics
            - rejection_messages: Pre-written fallback messages
            - system_instructions: Retrieval settings
    
    Raises:
        FileNotFoundError: If support_knowledge.json not found
        json.JSONDecodeError: If JSON is invalid
    """
    global _KB_CACHE
    
    if _KB_CACHE is not None:
        return _KB_CACHE
    
    # Get the directory of this file
    current_dir = Path(__file__).parent
    kb_path = current_dir / "support_knowledge.json"
    
    if not kb_path.exists():
        raise FileNotFoundError(
            f"Knowledge base not found at {kb_path}. "
            "Please ensure support_knowledge.json exists in the chatbot directory."
        )
    
    with open(kb_path, "r", encoding="utf-8") as f:
        _KB_CACHE = json.load(f)
    
    return _KB_CACHE


def extract_keywords(user_message: str) -> List[str]:
    """
    Extract and clean keywords from user message.
    
    Steps:
    1. Convert to lowercase
    2. Tokenize by whitespace and punctuation
    3. Remove stop words (common words that add no meaning)
    4. Remove single-character words
    5. Return cleaned keywords
    
    Args:
        user_message: User's raw input string
    
    Returns:
        List[str]: Cleaned keywords, e.g. ['lost', 'card', 'stolen']
    
    Example:
        >>> extract_keywords("My card is lost or stolen!")
        ['card', 'lost', 'stolen']
    """
    # Common stop words to filter out
    STOP_WORDS = {
        'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for',
        'if', 'in', 'is', 'it', 'of', 'or', 'the', 'to', 'with',
        'how', 'what', 'when', 'where', 'why', 'which',
        'can', 'could', 'should', 'would', 'do', 'does', 'did',
        'i', 'me', 'my', 'you', 'your', 'we', 'our', 'he', 'she', 'it',
        'on', 'that', 'this', 'not', 'no', 'yes'
    }
    
    # Convert to lowercase and replace punctuation with spaces
    text = user_message.lower()
    text = re.sub(r'[^\w\s]', ' ', text)  # Replace punctuation with space
    
    # Split into words
    tokens = text.split()
    
    # Filter: remove stop words and single-char tokens
    keywords = [
        token for token in tokens
        if token not in STOP_WORDS and len(token) > 1
    ]
    
    return keywords


def match_faq_entry(user_query: str, max_results: int = 3) -> List[Dict]:
    """
    Find relevant FAQ entries using keyword extraction and fuzzy matching.
    
    Retrieval Pipeline:
    1. Extract keywords from user query
    2. For each FAQ entry, compute best keyword match score
    3. Score = max(fuzzy_score) across all user keywords vs FAQ keywords
    4. Filter by min_score_threshold (0.5 = 50% similarity)
    5. Return top N matches sorted by score (descending)
    
    Uses fuzzywuzzy.fuzz.partial_ratio() for token-level matching:
    - partial_ratio: Best alignment of shorter string within longer string
    - Example: 'card' vs 'my card is lost' = 100 (full match)
    
    Args:
        user_query: User's question or statement
        max_results: Maximum number of matches to return (default: 3)
    
    Returns:
        List[Dict]: Matched FAQ entries with scores, sorted descending by score.
            Each entry includes: id, category, question, answer, score, keywords_matched
            Example:
            [
                {
                    'id': 'faq_lost_card',
                    'category': 'Card Management',
                    'question': 'What should I do if my card is lost?',
                    'answer': '...',
                    'score': 0.95,
                    'keywords_matched': ['lost', 'card']
                },
                ...
            ]
    
    Example:
        >>> results = match_faq_entry("My card is lost!")
        >>> len(results)
        1
        >>> results[0]['id']
        'faq_lost_card'
    """
    kb = load_knowledge_base()
    min_score = kb.get("system_instructions", {}).get("min_score_threshold", 0.5)
    high_confidence_threshold = 0.70  # Require at least one match to be high-quality
    
    # Extract keywords from user query
    user_keywords = extract_keywords(user_query)
    
    if not user_keywords:
        # If no keywords extracted (e.g., "?"), return empty
        return []
    
    # Score each FAQ entry
    scored_faqs = []
    
    for faq_entry in kb.get("faq", []):
        faq_keywords = faq_entry.get("keywords", [])
        
        if not faq_keywords:
            continue  # Skip entries without keywords
        
        # Find best match: max score across all user/FAQ keyword pairs
        best_score = 0.0
        matched_keywords = []
        has_high_confidence_match = False
        
        for user_kw in user_keywords:
            for faq_kw in faq_keywords:
                # Use partial_ratio for flexible token matching
                score = fuzz.partial_ratio(user_kw, faq_kw) / 100.0  # Normalize to 0-1
                
                if score > best_score:
                    best_score = score
                    
                    # Track high-confidence matches
                    if score >= high_confidence_threshold:
                        has_high_confidence_match = True
                    
                    if score >= min_score and user_kw not in matched_keywords:
                        matched_keywords.append(user_kw)
        
        # Only include if:
        # 1. Meets minimum threshold AND
        # 2. Has at least one high-confidence match (>70%) to filter gibberish
        if best_score >= min_score and has_high_confidence_match:
            scored_faqs.append({
                'id': faq_entry.get('id'),
                'category': faq_entry.get('category'),
                'question': faq_entry.get('question'),
                'answer': faq_entry.get('answer'),
                'scope': faq_entry.get('scope'),
                'account_specific': faq_entry.get('account_specific', False),
                'score': best_score,
                'keywords_matched': matched_keywords
            })
    
    # Sort by score descending
    scored_faqs.sort(key=lambda x: x['score'], reverse=True)
    
    # Return top N results
    return scored_faqs[:max_results]


def check_scope(user_message: str) -> Tuple[bool, Optional[str]]:
    """
    Check if user query is within scope of support agent.
    
    Scope validation:
    1. Check for account-specific keywords (balance, account number, pin, etc.)
       → Return rejection if detected
    2. Check for out-of-scope keywords (investment, tax, external topics)
       → Return rejection if detected
    3. Otherwise, return (True, None) - in scope
    
    Keywords are checked with token-level matching for accuracy.
    
    Args:
        user_message: User's question or statement
    
    Returns:
        Tuple[bool, Optional[str]]:
            - (True, None) if in scope - proceed with KB retrieval
            - (False, rejection_message) if out of scope - reject with reason
    
    Example:
        >>> is_valid, msg = check_scope("What's my account balance?")
        >>> is_valid
        False
        >>> msg
        "I appreciate your question, but I cannot access or discuss..."
    """
    kb = load_knowledge_base()
    rejection_msgs = kb.get("rejection_messages", {})
    
    user_lower = user_message.lower()
    
    # Account-specific keywords (personal data) - strict matching
    ACCOUNT_SPECIFIC_PATTERNS = [
        'balance', 'account number', 'account #', 'pin',
        'card number', 'cvv', 'routing number',
        'swift code', 'iban', 
        'transaction', 'statement', 'money'
    ]
    
    # Out-of-scope keywords (topics we don't support) - strict matching
    OUT_OF_SCOPE_PATTERNS = [
        'invest', 'stock', 'crypto', 'bitcoin', 'ethereum',
        'tax', 'taxes', 'filing', 'return', 'irs',
        'mortgage', 'refinance',
        'employment', 'job', 'visa', 'immigration',
        'insurance', 'retirement', 'pension',
        'real estate', 'property', 'rent',
        'weather', 'climate', 'temperature',  # Non-banking topics
        'sports', 'movies', 'politics', 'news'  # Completely unrelated
    ]
    
    # Check for account-specific keywords
    for pattern in ACCOUNT_SPECIFIC_PATTERNS:
        # Use both token_set_ratio and simple substring check for better coverage
        token_score = fuzz.token_set_ratio(user_lower, pattern)
        substring_match = pattern in user_lower  # Exact substring
        
        if token_score > 80 or substring_match:
            return False, rejection_msgs.get("account_specific", "")
    
    # Check for out-of-scope keywords  
    for pattern in OUT_OF_SCOPE_PATTERNS:
        token_score = fuzz.token_set_ratio(user_lower, pattern)
        substring_match = pattern in user_lower  # Exact substring
        
        if token_score > 80 or substring_match:
            return False, rejection_msgs.get("out_of_scope", "")
    
    # In scope
    return True, None


def build_context(matched_entries: List[Dict], bank_identity: Dict) -> str:
    """
    Format matched FAQ entries and bank identity into markdown context for LLM.
    
    Format:
    ```
    ═══════════════════════════════════════════════════════════════
    📚 VIRTUBANK KNOWLEDGE BASE
    ═══════════════════════════════════════════════════════════════
    
    Bank: VirtuBank | Contact: support@virtubank.example | Phone: +33...
    
    ───────────────────────────────────────────────────────────────
    [FAQ Topic 1]
    ───────────────────────────────────────────────────────────────
    
    Answer to FAQ...
    
    ───────────────────────────────────────────────────────────────
    [FAQ Topic 2]
    ───────────────────────────────────────────────────────────────
    
    Answer to FAQ...
    
    ═══════════════════════════════════════════════════════════════
    IMPORTANT: Answer ONLY using information above. Do not make up details.
    ═══════════════════════════════════════════════════════════════
    ```
    
    Args:
        matched_entries: List of matched FAQ dicts from match_faq_entry()
        bank_identity: Bank identity dict from KB
    
    Returns:
        str: Formatted markdown context string for LLM (target: ~1000-1500 tokens)
    
    Example:
        >>> matches = match_faq_entry("lost card")
        >>> ctx = build_context(matches, kb["bank_identity"])
        >>> print(ctx[:100])
        "═══════════════════════════════════════════"
    """
    lines = []
    
    # Header with bank identity
    lines.append("═" * 75)
    lines.append("📚 VIRTUBANK KNOWLEDGE BASE - SUPPORT AGENT")
    lines.append("═" * 75)
    lines.append("")
    
    # Bank contact info
    bank_name = bank_identity.get("name", "VirtuBank")
    contact_email = bank_identity.get("support_email", "support@virtubank.example")
    contact_phone = bank_identity.get("support_phone", "+33 (0) 800 123 456")
    support_hours = bank_identity.get("support_hours", "Mon-Fri 08:00-18:00 CET")
    
    lines.append(f"**Bank:** {bank_name}")
    lines.append(f"**Email:** {contact_email}")
    lines.append(f"**Phone:** {contact_phone}")
    lines.append(f"**Support Hours:** {support_hours}")
    lines.append("")
    
    # Add matched FAQ entries
    for i, entry in enumerate(matched_entries, 1):
        lines.append("─" * 75)
        lines.append(f"📌 {entry.get('category', 'General')} [{i}/{len(matched_entries)}]")
        lines.append("─" * 75)
        lines.append("")
        lines.append(f"**Q:** {entry.get('question', 'N/A')}")
        lines.append("")
        lines.append(f"**A:** {entry.get('answer', 'N/A')}")
        lines.append("")
    
    # Footer with constraints
    lines.append("═" * 75)
    lines.append("⚠️  CRITICAL INSTRUCTIONS:")
    lines.append("═" * 75)
    lines.append("")
    lines.append("1. Answer ONLY using information from the Knowledge Base above")
    lines.append("2. NEVER access or discuss customer personal account data")
    lines.append("3. If user asks out-of-scope questions, politely redirect to support")
    lines.append("4. Include VirtuBank contact info if user needs further assistance")
    lines.append("5. Maintain professional, helpful tone")
    lines.append("")
    
    return "\n".join(lines)


def handle_no_match(reason: str) -> str:
    """
    Return appropriate fallback message when no FAQ matches found or scope rejected.
    
    Reasons:
    - 'no_match': FAQ search returned zero results
    - 'account_specific': Query is about personal account data
    - 'out_of_scope': Query is outside support scope
    - 'fallback': Generic fallback for other errors
    
    Args:
        reason: Type of rejection (no_match, account_specific, out_of_scope, fallback)
    
    Returns:
        str: Pre-written fallback message from KB
    
    Example:
        >>> msg = handle_no_match('no_match')
        >>> print(msg[:50])
        "I'm not entirely sure how to help with..."
    """
    kb = load_knowledge_base()
    rejection_msgs = kb.get("rejection_messages", {})
    
    # Return appropriate message or default fallback
    return rejection_msgs.get(reason, rejection_msgs.get("fallback", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Testing / Debug Functions
# ─────────────────────────────────────────────────────────────────────────────

def debug_search(user_query: str, verbose: bool = True) -> Dict:
    """
    Debug function to trace through the retrieval pipeline step-by-step.
    
    Shows:
    - Keywords extracted
    - Scope check result
    - FAQ matches with scores
    - Final context (preview)
    
    Useful for testing and debugging retrieval logic.
    
    Args:
        user_query: User query to debug
        verbose: If True, print detailed output
    
    Returns:
        Dict with pipeline results for inspection
    """
    kb = load_knowledge_base()
    
    # Step 1: Extract keywords
    keywords = extract_keywords(user_query)
    
    # Step 2: Check scope
    is_valid, rejection = check_scope(user_query)
    
    # Step 3: Match FAQ
    matches = match_faq_entry(user_query)
    
    # Step 4: Build context (if matches found)
    context = build_context(matches, kb["bank_identity"]) if matches else ""
    
    result = {
        "query": user_query,
        "keywords_extracted": keywords,
        "scope_valid": is_valid,
        "rejection_message": rejection,
        "matches_found": len(matches),
        "matches": matches,
        "context_preview": context[:200] if context else "No context"
    }
    
    if verbose:
        print(f"\n{'='*75}")
        print(f"DEBUG SEARCH: {user_query}")
        print(f"{'='*75}")
        print(f"✓ Keywords: {keywords}")
        print(f"✓ In Scope: {is_valid}")
        if not is_valid:
            print(f"  ✗ Rejection: {rejection[:100]}...")
        print(f"✓ Matches: {len(matches)}")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. [{m['score']:.2%}] {m['id']} - {m['category']}")
        print(f"{'='*75}\n")
    
    return result


if __name__ == "__main__":
    # Quick test
    print("Testing support_knowledge module...\n")
    
    # Test 1: Load KB
    print("1. Loading knowledge base...")
    kb = load_knowledge_base()
    print(f"   ✓ Loaded {len(kb.get('faq', []))} FAQ entries\n")
    
    # Test 2: Extract keywords
    print("2. Testing keyword extraction...")
    test_queries = [
        "My card is lost!",
        "How do I reset my password?",
        "What's my account balance?"
    ]
    for q in test_queries:
        kw = extract_keywords(q)
        print(f"   Query: {q}")
        print(f"   Keywords: {kw}\n")
    
    # Test 3: Scope check
    print("3. Testing scope validation...")
    for q in test_queries:
        valid, msg = check_scope(q)
        print(f"   Query: {q}")
        print(f"   In Scope: {valid}\n")
    
    # Test 4: FAQ matching
    print("4. Testing FAQ matching...")
    for q in test_queries:
        matches = match_faq_entry(q)
        print(f"   Query: {q}")
        print(f"   Matches: {len(matches)}")
        for m in matches:
            print(f"     - {m['id']} ({m['score']:.1%})")
        print()
    
    print("All tests completed!")
