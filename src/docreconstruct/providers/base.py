"""Provider contracts shared by lightweight adapters and live extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from pydantic import Field, model_validator

from docreconstruct.ir import CanonicalModel, Document

ProviderInput: TypeAlias = str | bytes | bytearray | Path | Mapping[str, Any] | Sequence[Any]


class ProviderError(RuntimeError):
    """Base exception for provider failures."""


class ProviderInputError(ProviderError, ValueError):
    """Raised when a provider cannot understand a supplied payload."""


class ProviderDependencyError(ProviderError, ImportError):
    """Raised when an optional live-provider dependency is unavailable."""


class ProviderInferenceUnsupportedError(ProviderError, NotImplementedError):
    """Raised when an adapter is asked to run an engine it does not vendor."""


class ProviderExecutionMode(StrEnum):
    """Ways a provider can be used without implying that an engine is bundled."""

    SAVED = "saved"
    LOCAL = "local"
    API = "api"


class ProviderPrivacy(StrEnum):
    """Where document content is exposed while a provider executes."""

    UNKNOWN = "unknown"
    THIRD_PARTY = "third_party"
    USER_MANAGED = "user_managed"
    NO_TRANSFER = "no_transfer"


class ProviderCost(StrEnum):
    """Coarse operating-cost class used for deterministic provider routing."""

    FREE = "free"
    INFRASTRUCTURE = "infrastructure"
    METERED = "metered"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class ProviderCredentialRequirement(StrEnum):
    """Whether provider execution needs credentials supplied by the caller."""

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"
    UNKNOWN = "unknown"


class ProviderLicense(CanonicalModel):
    """License facts declared by an adapter without offering legal advice."""

    name: str = "unknown"
    spdx: str | None = None
    open_source: bool | None = None
    commercial_use: bool | None = None
    restrictions: list[str] = Field(default_factory=list)


class ProviderCapabilities(CanonicalModel):
    """Machine-readable declaration of evidence a provider can supply."""

    provider: str = Field(min_length=1)
    supported_inputs: list[str] = Field(default_factory=list)
    saved_json: bool = True
    live_inference: bool = False
    text: bool = True
    geometry: bool = False
    reading_order: bool = False
    styles: bool = False
    tables: bool = False
    images: bool = False
    multilingual: bool = False
    languages: list[str] = Field(default_factory=list)
    handwriting: bool = False
    formulas: bool = False
    charts: bool = False
    layout: bool = False
    distorted_photos: bool = False
    dewarping: bool = False
    execution_modes: list[ProviderExecutionMode] = Field(default_factory=list)
    markdown: bool = False
    bounding_boxes: bool = False
    confidence_scores: bool = False
    privacy: ProviderPrivacy = ProviderPrivacy.UNKNOWN
    license: ProviderLicense = Field(default_factory=ProviderLicense)
    model_name: str | None = None
    model_version: str | None = None
    cost: ProviderCost = ProviderCost.UNKNOWN
    credentials: ProviderCredentialRequirement = ProviderCredentialRequirement.UNKNOWN
    credential_env_vars: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _synchronize_legacy_fields(self) -> ProviderCapabilities:
        """Keep pre-capability-matrix declarations meaningful.

        ``saved_json``, ``live_inference`` and ``geometry`` predate the richer
        execution/output fields.  Deriving only unambiguous values lets older
        third-party declarations continue to participate in recommendation.
        """

        modes = list(dict.fromkeys(self.execution_modes))
        if self.saved_json and ProviderExecutionMode.SAVED not in modes:
            modes.append(ProviderExecutionMode.SAVED)
        if self.live_inference and not any(
            mode in modes for mode in (ProviderExecutionMode.LOCAL, ProviderExecutionMode.API)
        ):
            modes.append(ProviderExecutionMode.LOCAL)
        if ProviderExecutionMode.SAVED in modes and not self.saved_json:
            object.__setattr__(self, "saved_json", True)
        if (
            any(mode in modes for mode in (ProviderExecutionMode.LOCAL, ProviderExecutionMode.API))
            and not self.live_inference
        ):
            object.__setattr__(self, "live_inference", True)
        if modes != self.execution_modes:
            object.__setattr__(self, "execution_modes", modes)

        if self.geometry != self.bounding_boxes:
            enabled = self.geometry or self.bounding_boxes
            object.__setattr__(self, "geometry", enabled)
            object.__setattr__(self, "bounding_boxes", enabled)
        if self.reading_order and not self.layout:
            object.__setattr__(self, "layout", True)
        return self

    def __call__(self) -> ProviderCapabilities:
        """Allow both ``provider.capabilities`` and ``provider.capabilities()``."""

        return self


class ProviderContext(CanonicalModel):
    """Caller-supplied hints that are not part of a provider payload."""

    document_id: str | None = None
    source: str | None = None
    page_width: float | None = Field(default=None, gt=0)
    page_height: float | None = Field(default=None, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderResult(CanonicalModel):
    """Normalized provider result plus non-fatal adapter diagnostics."""

    provider: str = Field(min_length=1)
    document: Document
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Provider(ABC):
    """Provider interface; implementations always return canonical documents."""

    name: str

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Describe supported input and extracted evidence."""

    @abstractmethod
    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        """Normalize an already-loaded provider payload into a ``Document``."""

    @abstractmethod
    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        """Read/extract ``source`` and return a normalized result."""


class SavedJSONProvider(Provider):
    """Base class for adapters that only normalize previously saved results."""

    @abstractmethod
    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        raise NotImplementedError

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        from ._utils import load_json_source, looks_like_non_json_file

        if isinstance(source, (str, Path)) and looks_like_non_json_file(source):
            return self.infer(source, context=context)

        payload, source_label = load_json_source(source)
        effective_context = context or ProviderContext()
        if source_label and effective_context.source is None:
            effective_context = effective_context.model_copy(update={"source": source_label})
        document = self.normalize(payload, context=effective_context)
        return ProviderResult(provider=self.name, document=document)

    def infer(
        self,
        source: str | bytes | bytearray | Path,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        """Explain explicitly that heavyweight live inference is out of scope."""

        del source, context
        raise ProviderInferenceUnsupportedError(
            f"{type(self).__name__} normalizes saved {self.name} JSON/JSONL only; "
            f"live {self.name} inference is not bundled. Run the upstream engine "
            "and pass its saved result to parse()."
        )
