"""Cost-aware region router with selective ensemble escalation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from docreconstruct.ir import BBox, Document, Element, ElementType, Page, SourceType

from .models import (
    RoutingAction,
    RoutingPlan,
    RoutingPolicy,
    RoutingReason,
    RoutingTask,
)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _provider_registry() -> Any:
    try:
        from docreconstruct.providers import registry

        return registry
    except ImportError:
        return None


def _is_live(provider_name: str, registry: Any) -> bool:
    if provider_name == "preserve_source":
        return True
    if registry is None or provider_name not in registry:
        return False
    try:
        return bool(registry.get(provider_name).capabilities.live_inference)
    except (AttributeError, KeyError, RuntimeError):
        return False


def _ordered_unique(values: Iterable[str], *, excluding: str | None = None) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value != excluding and value not in result:
            result.append(value)
    return result


def _has_handwriting(element: Element) -> bool:
    metadata = element.metadata
    return bool(
        metadata.get("handwriting")
        or metadata.get("is_handwritten")
        or _value(metadata.get("script_type", "")) == "handwriting"
    )


def _has_disagreement(element: Element) -> bool:
    alternatives = {
        " ".join(candidate.value.split()).casefold() for candidate in element.text_candidates
    }
    return len(alternatives) > 1


def _provider_for_element(
    element: Element,
    policy: RoutingPolicy,
) -> tuple[str, list[str], list[RoutingReason]]:
    if _has_handwriting(element):
        return (
            policy.handwriting_provider,
            policy.fallback_providers.get("handwriting", []),
            [RoutingReason.HANDWRITING, RoutingReason.CONTENT_SPECIALIST],
        )
    if element.type is ElementType.TABLE:
        return (
            policy.table_provider,
            policy.fallback_providers.get("table", []),
            [RoutingReason.CONTENT_SPECIALIST],
        )
    if element.type is ElementType.FORMULA:
        return (
            policy.formula_provider,
            policy.fallback_providers.get("formula", []),
            [RoutingReason.CONTENT_SPECIALIST],
        )
    if element.metadata.get("complex_layout"):
        return (
            policy.complex_layout_provider,
            policy.fallback_providers.get("layout", []),
            [RoutingReason.COMPLEX_LAYOUT],
        )
    return (
        policy.ordinary_text_provider,
        policy.fallback_providers.get("ordinary", []),
        [],
    )


class DocumentRouter:
    """Plan the cheapest credible provider path, escalating only when needed."""

    def __init__(
        self,
        policy: RoutingPolicy | None = None,
        *,
        registry: Any = None,
    ) -> None:
        self.policy = policy or RoutingPolicy()
        self.registry = registry if registry is not None else _provider_registry()

    def _task(
        self,
        *,
        task_id: str,
        page: Page,
        bbox: BBox,
        content_type: ElementType,
        action: RoutingAction,
        primary: str,
        fallbacks: Iterable[str],
        reasons: list[RoutingReason],
        element: Element | None = None,
        require_consensus: bool = False,
        preserve_source_raster: bool = False,
    ) -> RoutingTask:
        alternatives = _ordered_unique(fallbacks, excluding=primary)
        providers_to_run = [primary]
        if require_consensus:
            providers_to_run.extend(alternatives[:2])
        cost = sum(self.policy.relative_costs.get(provider, 1.0) for provider in providers_to_run)
        return RoutingTask(
            id=task_id,
            page_number=page.number,
            element_id=element.id if element else None,
            bbox=bbox,
            content_type=content_type,
            action=action,
            primary_provider=primary,
            fallback_providers=alternatives,
            reasons=list(dict.fromkeys(reasons)),
            require_consensus=require_consensus,
            preserve_source_raster=preserve_source_raster,
            live_executable=_is_live(primary, self.registry),
            estimated_relative_cost=cost,
            metadata={
                "existing_provider": (
                    element.provenance.engine if element and element.provenance else None
                )
            },
        )

    def _initial_page_task(self, document: Document, page: Page) -> RoutingTask | None:
        raster_elements = [
            element
            for element in page.elements
            if element.type in {ElementType.IMAGE, ElementType.FIGURE}
        ]
        native_text = any(element.text for element in page.elements)
        if page.source_type is SourceType.NATIVE and native_text:
            return None
        if page.source_type is SourceType.NATIVE:
            return self._task(
                task_id=f"route-page-{page.number}-native",
                page=page,
                bbox=BBox(x0=0, y0=0, x1=page.width, y1=page.height),
                content_type=ElementType.UNKNOWN,
                action=RoutingAction.EXTRACT,
                primary=self.policy.native_provider,
                fallbacks=[],
                reasons=[RoutingReason.NATIVE_FIRST],
            )
        if page.source_type in {SourceType.IMAGE, SourceType.SCANNED} and not native_text:
            return self._task(
                task_id=f"route-page-{page.number}-ocr",
                page=page,
                bbox=BBox(x0=0, y0=0, x1=page.width, y1=page.height),
                content_type=ElementType.UNKNOWN,
                action=RoutingAction.EXTRACT,
                primary=self.policy.ordinary_text_provider,
                fallbacks=self.policy.fallback_providers.get("ordinary", []),
                reasons=[RoutingReason.INITIAL_EXTRACTION],
                preserve_source_raster=True,
            )
        if page.source_type is SourceType.HYBRID and not native_text and not raster_elements:
            return self._task(
                task_id=f"route-page-{page.number}-hybrid",
                page=page,
                bbox=BBox(x0=0, y0=0, x1=page.width, y1=page.height),
                content_type=ElementType.UNKNOWN,
                action=RoutingAction.EXTRACT,
                primary=self.policy.native_provider,
                fallbacks=[self.policy.ordinary_text_provider],
                reasons=[RoutingReason.NATIVE_FIRST, RoutingReason.INITIAL_EXTRACTION],
            )
        return None

    def plan(
        self,
        document: Document,
        *,
        force_element_ids: Iterable[str] = (),
    ) -> RoutingPlan:
        forced = set(force_element_ids)
        tasks: list[RoutingTask] = []
        for page in document.pages:
            initial = self._initial_page_task(document, page)
            if initial is not None:
                tasks.append(initial)
                # A full scanned page is intentionally routed once; its image
                # wrapper is not a second independent region.
                if page.source_type in {SourceType.IMAGE, SourceType.SCANNED}:
                    continue
            for element in page.elements:
                is_forced = element.id in forced
                disagreement = _has_disagreement(element)
                low_confidence = (
                    element.confidence is not None
                    and element.confidence < self.policy.confidence_threshold
                )
                is_special = element.type in {
                    ElementType.TABLE,
                    ElementType.FORMULA,
                    ElementType.CHART,
                } or _has_handwriting(element)
                if element.type in {ElementType.CHART, ElementType.IMAGE, ElementType.FIGURE}:
                    if is_forced or element.metadata.get("semantic_extraction"):
                        tasks.append(
                            self._task(
                                task_id=f"route-{page.number}-{element.id}-preserve",
                                page=page,
                                bbox=element.bbox,
                                content_type=element.type,
                                action=RoutingAction.PRESERVE,
                                primary="preserve_source",
                                fallbacks=[],
                                reasons=[RoutingReason.PRESERVE_VISUAL]
                                + ([RoutingReason.FORCED_REPAIR] if is_forced else []),
                                element=element,
                                preserve_source_raster=True,
                            )
                        )
                    continue
                if not (is_forced or disagreement or low_confidence or is_special):
                    continue
                primary, fallbacks, specialist_reasons = _provider_for_element(element, self.policy)
                existing = element.provenance.engine if element.provenance else None
                if (low_confidence or disagreement) and existing == primary:
                    choices = _ordered_unique(fallbacks, excluding=existing)
                    if choices:
                        primary, fallbacks = choices[0], [*choices[1:], existing]
                reasons = specialist_reasons
                if low_confidence:
                    reasons.append(RoutingReason.LOW_CONFIDENCE)
                if disagreement:
                    reasons.append(RoutingReason.PROVIDER_DISAGREEMENT)
                if is_forced:
                    reasons.append(RoutingReason.FORCED_REPAIR)
                require_consensus = bool(
                    self.policy.enable_consensus_on_disagreement
                    and (low_confidence or disagreement)
                )
                tasks.append(
                    self._task(
                        task_id=f"route-{page.number}-{element.id}",
                        page=page,
                        bbox=element.bbox,
                        content_type=element.type,
                        action=(
                            RoutingAction.ADJUDICATE
                            if disagreement
                            else RoutingAction.RETRY
                            if low_confidence or is_forced
                            else RoutingAction.EXTRACT
                        ),
                        primary=primary,
                        fallbacks=fallbacks,
                        reasons=reasons or [RoutingReason.CONTENT_SPECIALIST],
                        element=element,
                        require_consensus=require_consensus,
                        preserve_source_raster=element.type is ElementType.TABLE,
                    )
                )

        warnings: list[str] = []
        unavailable = sorted(
            {
                task.primary_provider
                for task in tasks
                if not task.live_executable and task.primary_provider != "preserve_source"
            }
        )
        if unavailable:
            warnings.append(
                "Live inference is not available for routed provider(s): "
                + ", ".join(unavailable)
                + ". Run the upstream engine or install a live provider plugin."
            )
        return RoutingPlan(
            document_id=document.id,
            policy=self.policy,
            tasks=tasks,
            estimated_relative_cost=sum(task.estimated_relative_cost for task in tasks),
            warnings=warnings,
        )


def build_routing_plan(
    document: Document,
    *,
    policy: RoutingPolicy | None = None,
    force_element_ids: Iterable[str] = (),
    registry: Any = None,
) -> RoutingPlan:
    return DocumentRouter(policy, registry=registry).plan(
        document, force_element_ids=force_element_ids
    )
