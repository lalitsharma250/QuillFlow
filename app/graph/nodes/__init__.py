"""
app/graph/nodes — Individual node implementations.

Each node is a thin wrapper:
  1. Read what it needs from GraphState
  2. Call the appropriate service
  3. Write results back to GraphState

Nodes do NOT contain business logic — that lives in services.
Nodes handle state marshalling and error handling.
"""
