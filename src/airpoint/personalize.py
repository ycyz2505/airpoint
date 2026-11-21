from __future__ import annotations

import json
import math
from pathlib import Path

from .paths import data_dir

LABELS = ("open", "pinch", "fist", "point")
DIMENSIONS = 10


def features(landmarks) -> list[float] | None:
    """Scale-free hand shape: ratios to the palm, so distance drops out."""
    if not landmarks or len(landmarks) < 21:
        return None
    palm = math.dist(landmarks[0], landmarks[9])
    if palm < 1e-6:
        return None
    ratio = lambda a, b: math.dist(landmarks[a], landmarks[b]) / palm
    return [
        ratio(4, 8),
        ratio(4, 0),
        ratio(8, 0),
        ratio(12, 0),
        ratio(16, 0),
        ratio(20, 0),
        ratio(8, 12),
        ratio(8, 16),
        ratio(4, 20),
        ratio(5, 17),
    ]


class Personalizer:
    """kNN over a few recorded samples; a third opinion, never the only one."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "gestures.json")
        self._samples: list[tuple[str, list[float]]] = []
        self._scale: list[float] = [1.0] * DIMENSIONS
        self._dirty = False
        self.load()

    def __len__(self) -> int:
        return len(self._samples)

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for label, _ in self._samples:
            tally[label] = tally.get(label, 0) + 1
        return tally

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        samples = []
        for item in data.get("samples", []):
            label = str(item.get("label", ""))
            vector = item.get("f")
            if (
                label in LABELS
                and isinstance(vector, list)
                and len(vector) == DIMENSIONS
            ):
                samples.append(
                    (label, [float(value) for value in vector])
                )
        self._samples = samples
        self._rescale()

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {
            "version": 1,
            "samples": [
                {"label": label, "f": vector}
                for label, vector in self._samples
            ],
        }
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            self._dirty = False
        except OSError:
            pass

    def add(self, label: str, vector: list[float] | None) -> bool:
        if label not in LABELS or not vector or len(vector) != DIMENSIONS:
            return False
        self._samples.append((label, [float(value) for value in vector]))
        self._dirty = True
        self._rescale()
        return True

    def clear(self) -> None:
        self._samples = []
        self._scale = [1.0] * DIMENSIONS
        self._dirty = True
        self.save()

    def _rescale(self) -> None:
        """Per-dimension spread, so a wide-range feature cannot dominate."""
        if len(self._samples) < 4:
            self._scale = [1.0] * DIMENSIONS
            return
        scale = []
        for index in range(DIMENSIONS):
            values = [vector[index] for _, vector in self._samples]
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(
                values
            )
            scale.append(max(0.05, math.sqrt(variance)))
        self._scale = scale

    def classify(
        self, vector: list[float] | None, k: int = 5
    ) -> dict[str, float]:
        """Return normalised label scores, or an empty dict when unusable."""
        if not vector or len(vector) != DIMENSIONS:
            return {}
        if len(self._samples) < 4:
            return {}
        scored = []
        for label, sample in self._samples:
            total = 0.0
            for index in range(DIMENSIONS):
                delta = (vector[index] - sample[index]) / self._scale[index]
                total += delta * delta
            scored.append((math.sqrt(total), label))
        scored.sort(key=lambda item: item[0])
        votes: dict[str, float] = {}
        for distance, label in scored[: max(1, int(k))]:
            votes[label] = votes.get(label, 0.0) + 1.0 / (distance + 0.15)
        total = sum(votes.values())
        if total <= 0.0:
            return {}
        return {label: weight / total for label, weight in votes.items()}

    def best(self, vector, k: int = 5) -> tuple[str, float]:
        scores = self.classify(vector, k)
        if not scores:
            return "", 0.0
        label = max(scores, key=scores.get)
        return label, scores[label]
