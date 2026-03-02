from typing import TypedDict, Annotated, List
from langchain_core.messages import BaseMessage
import operator

class BankChatState(TypedDict):
    messages:    Annotated[List[BaseMessage], operator.add] # the full chat history (accumulates with operator.add)
    user_id:     str       #who is talking
    session_id:  str       #which conversation  
    intent:      str        #what the user want (detect_intent) "account", "transfer", "support", ...
    agent:       str        # which agent handled it (filled by agent nodes) agent sélectionné
    context:     dict       # données bancaires contextuelles
    error:       str | None  # message d'erreur à afficher à l'utilisateur (remplit en cas d'exception)