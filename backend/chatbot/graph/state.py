from typing import TypedDict, Annotated, List
from langchain_core.messages import BaseMessage
import operator

class BankChatState(TypedDict):
    messages:    Annotated[List[BaseMessage], operator.add]
    user_id:     str
    session_id:  str
    intent:      str        # "account", "transfer", "support", "fraud", ...
    agent:       str
    context:     dict
    error:       str | None