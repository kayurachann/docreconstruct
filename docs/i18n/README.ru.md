# docreconstruct

[English](../../README.md) · [Tiếng Việt](README.vi.md) · [简体中文](README.zh-CN.md) · Русский

Реконструкция сканов в **редактируемые** DOCX/HTML/Markdown с сохранением
вёрстки. Экспериментальный проект.

Измеренные цифры (не обещания): визуальное сходство 23–57% на трёх
показательных примерах; реальный четырёхстраничный экзамен проходит 40/40
QA-проверок, 22 нативные формулы Office Math. Архитектура, бенчмарки и честные
ограничения — в [английском README](../../README.md); этот перевод содержит
только быстрый старт.

## Быстрый старт

```bash
pip install -e ".[all]"
```

Максимальное качество — три источника (проверенный Markdown + оригинальный
скан + OCR JSON):

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -E evidence.json -o out.docx
```

Один файл на входе, DOCX на выходе (качество ограничено вашим OCR-движком):

```bash
python -m docreconstruct.cli reconstruct scan.pdf -o out.docx
```

Оценка результата против исходного изображения той же метрикой, что и в CI:

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -o out.docx --qa-backend libreoffice
```

Ключевой принцип: **никогда не выдумывать текст по пикселям** — проверенный
Markdown является источником содержания; ошибки в нём сохраняются, а не
"исправляются" втихую.

Лицензия Apache-2.0. Изображения примеров принадлежат их издателям.
