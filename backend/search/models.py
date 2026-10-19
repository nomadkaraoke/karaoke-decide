"""Search service response models.

These names and fields are a contract: the mobile TS client is generated from the
OpenAPI snapshot of these models. Rename nothing without regenerating the client.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IdentificationStatus = Literal["confident", "candidates", "none"]
IdentifyKind = Literal["same", "cosmetic", "completed", "content"]
VersionKind = Literal["community", "commercial"]

# Largest accepted SearchEvent.data, as compact JSON.
MAX_EVENT_DATA_BYTES = 4096


class SongRef(BaseModel):
    """One real song. ``song_key`` is ``text_normalize.song_key`` of its song_id.db
    display spelling, the key availability groups are stored under."""

    artist: str
    title: str
    song_key: str
    popularity: int | None = None
    karaoke: bool


class Version(BaseModel):
    brand_code: str
    brand_name: str
    kind: VersionKind
    youtube_url: str | None = None
    rank: int


class SongGroup(BaseModel):
    """A song with karaoke versions, best first. ``version_count`` counts them all,
    even when ``versions`` is capped."""

    song_key: str
    artist: str
    title: str
    version_count: int
    versions: list[Version]


class Identification(BaseModel):
    status: IdentificationStatus
    # Only set when status == "confident".
    kind: IdentifyKind | None = None
    best: SongRef | None = None
    candidates: list[SongRef] = Field(default_factory=list)


class IndexInfo(BaseModel):
    run: str
    built_at: int


class SearchResponse(BaseModel):
    identification: Identification
    groups: list[SongGroup]
    index: IndexInfo
    sid: str


class SongDetail(BaseModel):
    song: SongRef
    versions: list[Version]


class Split(BaseModel):
    artist: str
    title: str


class ResolveResponse(BaseModel):
    """kjbox ``/sing/search/resolve`` shape. Every field is optional: ``{}`` means
    "nothing to offer" (including any failure of the gen call)."""

    corrected: SongRef | None = None
    kind: Literal["cosmetic", "content"] | None = None
    typed: str | None = None
    groups: list[SongGroup] = Field(default_factory=list)
    split: Split | None = None
    alternatives: list[SongRef] = Field(default_factory=list)


class SearchEvent(BaseModel):
    sid: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def _small_data(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, separators=(",", ":"), default=str)) > MAX_EVENT_DATA_BYTES:
            raise ValueError(f"data larger than {MAX_EVENT_DATA_BYTES} bytes")
        return v


class OfflineIndexManifest(BaseModel):
    """Where the phone downloads ``offline.db.gz`` for the loaded run."""

    model_config = ConfigDict(populate_by_name=True)

    run: str
    # "schema" shadows a BaseModel attribute, hence the alias; the wire name is "schema".
    schema_: int = Field(alias="schema")
    normalizer: int
    url: str
    bytes: int
    sha256: str


class Health(BaseModel):
    ok: bool
