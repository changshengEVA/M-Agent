"""Allow ``python -m m_agent.lab.stimulus`` and ``python -m m_agent.lab``."""

from m_agent.lab.stimulus import main

if __name__ == "__main__":
    raise SystemExit(main())
