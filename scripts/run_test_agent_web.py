from __future__ import annotations

from _bootstrap import bootstrap_project


bootstrap_project()

from m_agent.chat.web_ui import main


if __name__ == "__main__":
    main()
