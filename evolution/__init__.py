"""Control-plane helpers for evidence-gated evaluation evolution.

Loop A: test assets evolve; evaluated targets and scoring remain fixed.
"""

from .target_loop import compare_target, register_target_candidate

__all__ = ["compare_target", "register_target_candidate"]
