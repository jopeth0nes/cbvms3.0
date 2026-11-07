"""Optional printed-logo contract. No trained backend is supplied by Stage 5.

The admission/deadline guard is usable by a future observation-only backend.
It owns no thread, camera, cache, predictor, policy or production model pointer.
Production configuration rejects enablement until a validated backend exists.
"""
from dataclasses import dataclass
import math
import threading
import uuid


NOT_TRAINED = 'Not trained'


@dataclass(frozen=True)
class LogoVerificationConfig:
    enabled: bool = False
    # Scheduling budget, NOT an accuracy criterion or measured latency promise.
    max_added_latency_ms: float = 100.

    @classmethod
    def parse(cls, value=None):
        if value is None:
            return cls()
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown logo verification configuration')
        result = cls(**value)
        result.validate()
        return result

    def validate(self):
        if type(self.enabled) is not bool:
            raise ValueError('Logo enabled must be a boolean')
        budget = self.max_added_latency_ms
        if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 1000:
            raise ValueError('Logo scheduling budget must be greater than 0 and at most 1000 ms')

    def require_available(self):
        self.validate()
        if self.enabled:
            raise ValueError('Logo verifier: Not trained. Enablement requires reviewed photographed logos, '
                             'a compatible trained bundle and paired held-out evaluation against explicit criteria.')


@dataclass(frozen=True)
class LogoRequest:
    token: str
    epoch: int
    ticket: object
    box: tuple
    started_at: float
    deadline: float


def original_logo_region(frame, ticket, box):
    """View the associated original raster, with no resize, flip or OCR."""
    if not ticket.usable or ticket.torso_box is None:
        raise ValueError('Logo needs a usable associated torso')
    if not isinstance(box, (tuple, list)) or len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError('Logo box needs original-raster integer coordinates')
    a, b, c, d = ticket.torso_box
    x1, y1, x2, y2 = box
    h, w = frame.shape[:2]
    if not (0 <= a <= x1 < x2 <= c <= w and 0 <= b <= y1 < y2 <= d <= h):
        raise ValueError('Logo must belong wholly to the current torso')
    return frame[y1:y2, x1:x2]


class LogoRequestGate:
    """One outstanding request, no waiting queue and no stored image/cache.

    This is an admission contract, not an inference executor. Tests use an
    explicitly available fixture backend. The application has no such backend.
    A future executor must enforce cooperative cancellation or process isolation;
    rejecting a late result alone does not bound a blocking model call.
    """
    def __init__(self, config, *, backend_available=False):
        config.validate()
        self.config = config
        self.backend_available = backend_available
        self._lock = threading.Lock()
        self._active = None
        self._epoch = 0

    def reserve(self, *, frame, ticket, logo_box, visibility, torso_label,
                now, frame_ttl, current):
        with self._lock:
            if (not self.config.enabled or not self.backend_available or self._active is not None
                    or torso_label is not None or visibility != 'visible'):
                return None
            if (not math.isfinite(now) or not math.isfinite(frame_ttl) or frame_ttl <= 0
                    or not 0 <= now-ticket.captured_at < frame_ttl or not current(ticket, now)):
                return None
            original_logo_region(frame, ticket, logo_box)  # Validate association before reservation.
            request = LogoRequest(uuid.uuid4().hex, self._epoch, ticket, tuple(logo_box), now,
                min(ticket.captured_at+frame_ttl, now+self.config.max_added_latency_ms/1000))
            self._active = request
            return request

    def finish(self, request, *, now, current):
        """Release only the matching job; stale/late completion supplies no evidence."""
        with self._lock:
            if request is not self._active or request is None:
                return False
            self._active = None
            return bool(request.epoch == self._epoch and math.isfinite(now)
                        and request.started_at <= now <= request.deadline
                        and current(request.ticket, now))

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            # Retain the occupied slot until its owner acknowledges completion.
            # Otherwise source switching could launch overlapping model calls.


def unavailable_logo_status(config=None):
    config = config or LogoVerificationConfig()
    return {'status': NOT_TRAINED, 'enabled': False, 'executed': False,
            'max_added_latency_ms': config.max_added_latency_ms,
            'reason': 'No trained and evaluated printed-logo verifier. Torso-only assessment remains in use.'}
