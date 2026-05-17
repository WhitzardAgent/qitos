"""Interactive REPL for any QitOS AgentModule.

Provides ``AgentREPL`` — a full-featured interactive REPL with streaming
output, permission confirmation, markdown rendering, slash commands, and
multi-turn conversation. Works with any ``AgentModule`` out of the box.

Usage::

    from qitos.kit.repl import AgentREPL
    from my_agent import MyAgent

    repl = AgentREPL(agent=MyAgent(llm=llm), workspace=".")
    repl.run()
"""

from .core import AgentREPL

__all__ = ["AgentREPL"]
