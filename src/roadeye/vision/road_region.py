"""Where in the frame a road defect is allowed to be.

A forward-facing camera on a rigid mount sees more than road. It sees sky, buildings,
pavements, parked cars, the vehicle's own bonnet, and other people's driveways. A
detector trained on road damage will happily find crack-shaped things in all of them: a
shadow on a kerb, a joint in a wall, a repair on private land. Each one becomes a
``PROBABLE`` defect that a human has to open, look at, and reject.

That is the cost this module exists to cut. **Where a detection sits in the frame is
information the pipeline currently throws away**, and for a rigidly mounted camera it is
strong information: the road is in front of the vehicle, below the horizon, and roughly
trapezoidal under perspective.

## Why geometry and not a segmentation model

A road-surface segmentation network is the more sophisticated answer and is the intended
upgrade — behind this same seam. It is not the answer today, for two reasons.

torchvision's DeepLabV3 checkpoints are trained on COCO-with-VOC labels, whose 21 classes
are ``aeroplane`` through ``tvmonitor``. **There is no road class in them.** A road class
means a Cityscapes-trained checkpoint, which is a different model with its own licence
question, and RoadEye does not adopt weights before answering that (``docs/LICENSE_AUDIT.md``).

And a trapezoid is *inspectable*. A reviewer can be shown the region, disagree with it,
and change four numbers. When a segmentation network drops a real pothole nobody can see
why. For the first Yerevan survey, the debuggable filter is worth more than the clever one.

## What this must never become

A region is a statement about **the camera**, not about the road. A detection outside it
is not "not a defect" — it is something we chose not to analyse, and the run says so by
counting it. Silently deleting detections would be the same structural dishonesty
``roadeye.reporting.segments`` exists to prevent, moved one stage earlier.

Which is why there is no default region. The geometry depends on where the phone is
mounted, and no phone has been mounted yet (``docs/COLLECTION_PROTOCOL.md``). Filtering
with guessed numbers before anyone has seen a real frame would delete real defects and
call it precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoadRegion:
    """A trapezoid, in fractions of frame size, where road damage may be reported.

    Coordinates are normalised (0.0-1.0) so one region survives a change of camera
    resolution. ``y`` runs downward from the top of the frame, matching image
    convention and :class:`~roadeye.domain.models.BoundingBox`.

    ::

           0.0 ┌──────────────────────────┐
               │           sky            │
      horizon ─┤      ╱────────────╲      │   far_left … far_right
               │    ╱   road ahead   ╲    │
               │  ╱                    ╲  │
       bonnet ─┤────────────────────────  │   near_left … near_right
           1.0 └──────────────────────────┘   the vehicle's own bonnet
    """

    #: Nothing above this fraction of frame height is road. Sky, buildings, upper walls.
    horizon: float = 0.45
    #: Fraction of frame height occupied by the bonnet/dashboard at the bottom. The
    #: camera sees the car itself, and a "defect" there is a reflection.
    bonnet: float = 0.05
    #: Horizontal extent of the road at the near edge (just above the bonnet).
    near_left: float = 0.0
    near_right: float = 1.0
    #: Horizontal extent at the horizon. Narrower than the near edge: that is
    #: perspective, and it is what keeps pavements and shopfronts out.
    far_left: float = 0.35
    far_right: float = 0.65

    def __post_init__(self) -> None:
        for name in ("horizon", "bonnet", "near_left", "near_right", "far_left", "far_right"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a fraction of frame size, got {value}")
        if self.near_left >= self.near_right:
            raise ValueError("near_left must be left of near_right")
        if self.far_left >= self.far_right:
            raise ValueError("far_left must be left of far_right")
        # horizon + bonnet must leave a band to analyse; an empty region would silently
        # reject every detection, which looks exactly like a detector that found nothing.
        if self.horizon >= 1.0 - self.bonnet:
            raise ValueError(
                f"horizon ({self.horizon}) leaves no road above the bonnet "
                f"({self.bonnet}) — this region would reject every detection"
            )

    def contains(self, x: float, y: float) -> bool:
        """Is this normalised point inside the trapezoid?"""
        near_y = 1.0 - self.bonnet
        if y < self.horizon or y > near_y:
            return False

        # How far down between horizon and near edge, 0 at the horizon, 1 at the bonnet.
        span = near_y - self.horizon
        t = 1.0 if span <= 0 else (y - self.horizon) / span

        left = self.far_left + (self.near_left - self.far_left) * t
        right = self.far_right + (self.near_right - self.far_right) * t
        return left <= x <= right

    def accepts(self, x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> bool:
        """Does this box sit on the road?

        Tested at the **bottom centre** of the box, not its centre. For anything resting
        on the ground, that is the point where the object meets the surface — a pothole
        seen obliquely has its far edge higher in the frame than its near edge, and the
        centre floats above the road. The same choice is what keeps a tall object
        (a wall, a parked van) out: its ground contact is at its base, which is where it
        actually stands.
        """
        if width <= 0 or height <= 0:
            return True  # Unknown frame size: refuse to guess rather than reject.
        return self.contains((x1 + x2) / 2.0 / width, max(y1, y2) / height)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "bonnet": self.bonnet,
            "near_left": self.near_left,
            "near_right": self.near_right,
            "far_left": self.far_left,
            "far_right": self.far_right,
        }


#: A defensible starting shape for a phone clamped to a windscreen, looking forward and
#: slightly down. **These are guessed numbers, not measured ones** — the same status as
#: the map-matching thresholds (``docs/MAP_MATCHING.md``). They exist so there is
#: something to argue with after the first drive, and they are not enabled by default.
WINDSCREEN_MOUNT = RoadRegion()
