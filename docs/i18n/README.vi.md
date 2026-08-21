# docreconstruct

[English](../../README.md) · Tiếng Việt · [简体中文](README.zh-CN.md) · [Русский](README.ru.md)

Tái tạo tài liệu scan thành DOCX/HTML/Markdown **chỉnh sửa được**, giữ bố cục.
Đang thử nghiệm.

Con số đo được (không phải hứa hẹn): độ giống trực quan 23–57% trên ba
showcase; đề thi 4 trang thật đạt 40/40 tiêu chí QA với 22 công thức Office
Math gốc. Chi tiết, kiến trúc và giới hạn trung thực: xem
[README tiếng Anh](../../README.md) — bản dịch này chỉ gồm phần bắt đầu nhanh.

## Bắt đầu nhanh

```bash
pip install -e ".[all]"
```

Chất lượng tốt nhất — ba nguồn (Markdown đã duyệt + bản scan gốc + JSON OCR):

```bash
python -m docreconstruct.cli hybrid noidung.md goc.pdf -E evidence.json -o ketqua.docx
```

Một file vào, DOCX ra (chất lượng phụ thuộc engine OCR bạn có):

```bash
python -m docreconstruct.cli reconstruct scan.pdf -o ketqua.docx
```

Chấm điểm kết quả so với ảnh gốc bằng đúng metric CI dùng:

```bash
python -m docreconstruct.cli hybrid noidung.md goc.pdf -o ketqua.docx --qa-backend libreoffice
```

Nguyên tắc cốt lõi: **không bao giờ bịa chữ từ ảnh** — Markdown đã duyệt là
nguồn nội dung; lỗi trong đó được giữ nguyên thay vì bị "sửa" ngầm.

Giấy phép Apache-2.0. Ảnh showcase thuộc về nguồn phát hành gốc.
