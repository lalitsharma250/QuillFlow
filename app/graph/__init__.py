"""
app/graph — LangGraph DAG orchestration for QuillFlow.

The graph defines the execution flow for every query:
  Input Filter → Cache Check → Router → Retriever →
    → [Simple: Direct Answer]
    → [Complex: Planner → Parallel Writers → Reducer]
  → Validator → Cache Write → Output

Components:
  - state.py:   Typed state flowing through every node
  - nodes/:     Individual node implementations (thin wrappers around services)
  - edges.py:   Conditional routing logic
  - builder.py: Assembles the complete DAG
"""
