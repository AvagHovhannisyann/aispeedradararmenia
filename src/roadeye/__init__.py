"""RoadEye — smartphone-based road inspection.

A survey is one drive: forward-facing video plus synchronised location. An offline
pipeline turns that into a map of probable road defects, each traceable back to the
frame, the model and the configuration that produced it.

Design invariants, enforced by tests and stated here because they are the parts most
easily lost during a refactor:

1. **The domain layer imports no ML framework.** Detectors sit behind a Protocol
   (:mod:`roadeye.vision.base`), so the entire pipeline runs on a CPU-only machine with
   no model weights and no ffmpeg.
2. **The phone's GPS is the camera's position, never the defect's.** Every coordinate
   carries a :class:`~roadeye.domain.enums.LocationMethod` and an uncertainty.
3. **Detections are not defects.** One pothole seen in twenty frames is one defect with
   twenty observations.
4. **Only humans mark a defect verified.** A machine may write ``PROBABLE`` and nothing
   stronger.
5. **RoadEye requires no paid AI API at runtime** (ADR-005).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
