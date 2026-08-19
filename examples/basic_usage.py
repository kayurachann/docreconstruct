"""Load canonical IR and render it without installing an OCR engine."""

from pathlib import Path

from docreconstruct import Document, build_routing_plan, export


def main() -> None:
    repository = Path(__file__).resolve().parent.parent
    source = repository / "examples" / "example_document.json"
    destination = repository / "output" / "example.html"

    document = Document.from_json(source.read_text(encoding="utf-8"))
    routing = build_routing_plan(document)
    written = export(document, destination, output_format="html")
    print(f"Rendered {len(document.pages)} page(s) to {written}")
    print(f"Selective routing tasks: {len(routing.tasks)}")


if __name__ == "__main__":
    main()
