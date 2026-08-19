"""Provider for canonical DocumentGraph JSON."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from docreconstruct.ir import Document

from ._utils import document_id
from .base import (
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    ProviderResult,
    SavedJSONProvider,
)


class JSONProvider(SavedJSONProvider):
    """Load and validate the canonical v0.1 JSON representation."""

    name = "json"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["json", "jsonl"],
        saved_json=True,
        text=True,
        geometry=True,
        reading_order=True,
        styles=True,
        tables=True,
        images=True,
        multilingual=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache License 2.0",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
        ),
        model_name="docreconstruct canonical IR",
        model_version="0.1",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        if isinstance(payload, ProviderResult):
            document = payload.document
        elif isinstance(payload, Document):
            document = payload
        else:
            if isinstance(payload, list) and len(payload) == 1:
                payload = payload[0]
            if isinstance(payload, dict):
                for key in ("document", "document_graph", "docir"):
                    if key in payload:
                        payload = payload[key]
                        break
            try:
                document = Document.model_validate(payload)
            except ValidationError as exc:
                raise ProviderInputError(
                    "input is not valid canonical document JSON: "
                    f"{exc.error_count()} validation error(s)"
                ) from exc

        updates: dict[str, Any] = {}
        if context is not None and context.document_id:
            updates["id"] = document_id(self.name, context)
        if context is not None and context.source and document.source is None:
            updates["source"] = context.source
        if updates:
            document = document.model_copy(update=updates)
        return document


# Friendly alternative spelling without weakening the stable acronym form.
JsonProvider = JSONProvider
