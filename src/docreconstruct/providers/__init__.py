"""Provider contracts, built-in lightweight adapters, and registry."""

from ._hosted import (
    HostedProviderError,
    HTTPResponse,
    HTTPTransport,
    ProviderAuthenticationError,
    ProviderHTTPError,
    RemoteInferenceDisabledError,
)
from .aws_textract import (
    AmazonTextractProvider,
    AWSTextractProvider,
    AwsTextractProvider,
    TextractProvider,
)
from .azure_document_intelligence import (
    AzureDocumentAIProvider,
    AzureDocumentIntelligenceProvider,
)
from .base import (
    Provider,
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderDependencyError,
    ProviderError,
    ProviderExecutionMode,
    ProviderInferenceUnsupportedError,
    ProviderInput,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    ProviderResult,
    SavedJSONProvider,
)
from .google_document_ai import (
    GoogleCloudDocumentAIProvider,
    GoogleDocumentAIProvider,
    GoogleDocumentAiProvider,
)
from .json_provider import JSONProvider, JsonProvider
from .markdown import MarkdownEvidenceProvider, MarkdownProvider
from .mathpix import MathpixOCRProvider, MathpixOcrProvider, MathpixProvider
from .mineru import MinerUProvider, MineruProvider
from .mistral_ocr import MistralOCRProvider, MistralOcrProvider
from .native_pdf import NativePDFProvider, NativePdfProvider
from .olmocr import OLMOCRProvider, OlmOCRProvider, OlmOcrProvider
from .paddleocr import PaddleOCRProvider, PaddleOcrProvider
from .paddleocr_official import PaddleOCRAPIProvider, PaddleOCROfficialProvider
from .paddleocr_vl_server import (
    PaddleOCRVLAPIProvider,
    PaddleOCRVLServerProvider,
    PaddleOcrVlServerProvider,
)
from .registry import ProviderRegistry, get_registry
from .selection import (
    CapabilityRequest,
    ProviderRecommendation,
    recommend_providers,
    select_provider,
)

registry = get_registry()
for _provider in (
    JSONProvider,
    MarkdownEvidenceProvider,
    MathpixProvider,
    NativePDFProvider,
    PaddleOCRProvider,
    PaddleOCROfficialProvider,
    PaddleOCRVLServerProvider,
    MinerUProvider,
    OlmOCRProvider,
    MistralOCRProvider,
    AzureDocumentIntelligenceProvider,
    AWSTextractProvider,
    GoogleDocumentAIProvider,
):
    if _provider.name not in registry:
        registry.register(_provider)


def get_provider(name: str) -> Provider:
    """Instantiate or return a registered provider by name."""

    return registry.get(name)


def register_provider(
    provider: Provider | type[Provider],
    *,
    name: str | None = None,
    replace: bool = False,
    capabilities: ProviderCapabilities | None = None,
) -> Provider | type[Provider]:
    """Register a provider in the process-wide registry."""

    return registry.register(
        provider,
        name=name,
        replace=replace,
        capabilities=capabilities,
    )


__all__ = [
    "AmazonTextractProvider",
    "AWSTextractProvider",
    "AwsTextractProvider",
    "GoogleCloudDocumentAIProvider",
    "GoogleDocumentAIProvider",
    "GoogleDocumentAiProvider",
    "JSONProvider",
    "JsonProvider",
    "MarkdownEvidenceProvider",
    "MarkdownProvider",
    "MathpixOCRProvider",
    "MathpixOcrProvider",
    "MathpixProvider",
    "AzureDocumentAIProvider",
    "AzureDocumentIntelligenceProvider",
    "HostedProviderError",
    "HTTPResponse",
    "HTTPTransport",
    "MinerUProvider",
    "MineruProvider",
    "MistralOCRProvider",
    "MistralOcrProvider",
    "NativePDFProvider",
    "NativePdfProvider",
    "OLMOCRProvider",
    "OlmOCRProvider",
    "OlmOcrProvider",
    "PaddleOCRProvider",
    "PaddleOcrProvider",
    "PaddleOCRAPIProvider",
    "PaddleOCROfficialProvider",
    "PaddleOCRVLAPIProvider",
    "PaddleOCRVLServerProvider",
    "PaddleOcrVlServerProvider",
    "TextractProvider",
    "CapabilityRequest",
    "Provider",
    "ProviderCapabilities",
    "ProviderCost",
    "ProviderContext",
    "ProviderCredentialRequirement",
    "ProviderDependencyError",
    "ProviderError",
    "ProviderExecutionMode",
    "ProviderInferenceUnsupportedError",
    "ProviderInput",
    "ProviderInputError",
    "ProviderAuthenticationError",
    "ProviderHTTPError",
    "ProviderLicense",
    "ProviderPrivacy",
    "ProviderRecommendation",
    "ProviderRegistry",
    "ProviderResult",
    "RemoteInferenceDisabledError",
    "SavedJSONProvider",
    "get_provider",
    "get_registry",
    "register_provider",
    "recommend_providers",
    "registry",
    "select_provider",
]
