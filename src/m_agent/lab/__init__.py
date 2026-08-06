"""Offline labs and replay harnesses for Public Alpha."""

__all__ = ["StimulusLab", "run_stimulus_lab"]


def __getattr__(name: str):
    if name in {"StimulusLab", "run_stimulus_lab"}:
        from m_agent.lab.stimulus import StimulusLab, run_stimulus_lab

        return {
            "StimulusLab": StimulusLab,
            "run_stimulus_lab": run_stimulus_lab,
        }[name]
    raise AttributeError(name)