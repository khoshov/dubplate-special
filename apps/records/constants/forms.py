from typing import Tuple

SOURCE_DISCOGS = "discogs"
SOURCE_REDEYE = "redeye"
SOURCE_CHOICES: Tuple[Tuple[str, str], ...] = (
    (SOURCE_DISCOGS, "Discogs"),
    (SOURCE_REDEYE, "Redeye Records"),
)
