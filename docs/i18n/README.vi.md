# docreconstruct — hướng dẫn bằng tiếng Việt

[English](../../README.md) · **Tiếng Việt** · [简体中文](README.zh-CN.md) ·
[Русский](README.ru.md)

`docreconstruct` tái tạo PDF, bản quét và ảnh chụp tài liệu thành các tệp có
cấu trúc và có thể chỉnh sửa. Trong quy trình này, kết quả OCR chỉ là một nguồn
dữ liệu để đối chiếu, không phải sản phẩm cuối cùng. Mô hình tài liệu thống nhất,
bộ lập bố cục và bộ xuất tệp sẽ quyết định cách dựng DOCX, HTML hoặc JSON.

## Bộ đầu vào cho kết quả tốt nhất

Kết quả thường chính xác nhất khi có đủ ba loại đầu vào bổ trợ lẫn nhau. Mỗi
loại giữ một vai trò riêng và không thể thay thế hoàn toàn cho hai loại còn lại:

| Đầu vào | Dự án dùng làm chuẩn cho |
| --- | --- |
| `content.md` đã được rà soát | Nội dung văn bản và thứ tự đọc mong muốn; dự án không tự ý sửa câu chữ hoặc thêm nội dung |
| Một hoặc nhiều tệp `.json` từ dịch vụ OCR hoặc phân tích bố cục | Trang và khối nội dung tương ứng, tọa độ, loại nội dung, bảng, công thức, kiểu trình bày, độ tin cậy và thông tin nguồn gốc |
| PDF hoặc ảnh gốc | Khổ trang thực tế, hình thức trình bày, cột, bảng, hình minh họa và các vùng ảnh cần lấy lại |

Trước khi tổng hợp, dự án chuẩn hóa và đối chiếu riêng từng tệp JSON. JSON có
thể giúp xác định bố cục và cấu trúc, nhưng không được ghi đè câu chữ trong
Markdown. PDF hoặc ảnh gốc vẫn là căn cứ cuối cùng về hình thức và kích thước
trang. Tệp JSON không liên quan hoặc mâu thuẫn sẽ bị từ chối hoặc được nêu rõ
trong báo cáo QA. Nếu thiếu một trong ba nguồn, dự án vẫn có thể xử lý, nhưng sẽ
đối chiếu được ít yếu tố hơn và kết quả cần được xem là có độ tin cậy thấp hơn.

Theo mặc định, nhãn số trang phải khớp chính xác. Dự án chỉ ghép lại theo thứ tự
khi cả hai dãy trang đều đầy đủ, liên tiếp và có cùng số lượng — chẳng hạn các
trang OCR 5–6 tương ứng với hai trang ảnh cắt được đánh số 1–2. Việc ghép lại
luôn được ghi thành cảnh báo trong báo cáo kiểm tra. Dãy trang bị thiếu hoặc
không liên tục sẽ không bao giờ được suy đoán. Đơn vị tọa độ và thông tin tiền xử
lý của từng nguồn OCR cũng được giữ lại để có thể quy đổi vị trí về đúng trang
gốc.

```powershell
docreconstruct hybrid content.md original.png `
  --evidence paddleocr.json `
  --evidence mineru.json `
  --output output/result.docx `
  --qa-report output/result.qa.json
```

Có thể lặp lại `--evidence` để dùng kết quả từ nhiều nguồn độc lập. Nếu dự án
không nhận diện chắc chắn cấu trúc của tệp JSON, hãy chỉ rõ nguồn OCR thay vì để
dự án tự đoán:

```powershell
docreconstruct hybrid content.md original.pdf `
  --evidence result.json `
  --evidence-provider result.json=paddleocr `
  --output output/result.docx
```

### Tài liệu gốc có nhiều trang

Với PDF nhiều trang, dự án phân tích và lập bố cục riêng cho từng trang. Mỗi
trang gốc trở thành một phần tài liệu Word độc lập, giữ khổ trang tương ứng và
luôn bắt đầu ở trang mới. Một nhóm nội dung có thể tiếp tục sang trang kế tiếp
nếu có dữ liệu đối chiếu độc lập xác nhận; trang trắng hoặc trang bị OCR bỏ sót vẫn
được giữ thành một phần trống thay vì kéo nhầm nội dung từ trang sau lên.
QA mặc định kiểm tra số phần đã lập, còn QA bằng LibreOffice kiểm tra thêm số
trang kết xuất phải đúng bằng số trang của bản gốc.

```powershell
docreconstruct hybrid complete-document.md multi-page-original.pdf `
  --evidence provider-result.json `
  --output output/complete-document.docx `
  --qa-backend libreoffice `
  --qa-report output/complete-document.qa.json
```

## Cài đặt nhanh

```powershell
python -m pip install -e ".[hybrid]"
docreconstruct hybrid content.md original.png -o output/result.docx
```

LibreOffice chỉ được khởi chạy khi người dùng chủ động bật bước kết xuất để kiểm
tra hình ảnh:

```powershell
docreconstruct hybrid content.md original.png -o output/result.docx `
  --qa-backend libreoffice `
  --min-visual-score 0.80 `
  --qa-report output/result.qa.json
```

## Các ví dụ đã tái tạo thành công

Đây là kết quả thực tế từ cùng một quy trình chung mà lệnh CLI sử dụng. Bạn có
thể mở ảnh gốc, ảnh do dự án kết xuất từ DOCX và tệp Word chỉnh sửa được để tự
đối chiếu:

- **Tuyen Quang gifted school Math exam - Page 1 - Exam code: 0110** — Nguồn:
  [VietnamNet](https://vietnamnet.vn/).
- **Calculus derivation - editable Office Math** — Nguồn:
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR).
- **Tuyen Quang gifted school - Vietnamese 2nd exam** — Nguồn:
  [VNExpress](https://vnexpress.net/).

Ảnh gốc, ảnh kết xuất từ DOCX, tệp DOCX chỉnh sửa được và mã kiểm tra SHA-256
được lưu trong [thư mục ví dụ](../showcases/README.md). Thông tin nguồn do người
đóng góp cung cấp nhằm giúp truy vết tài liệu; thông tin này không phải giấy phép
tái sử dụng và cũng không có nghĩa đơn vị xuất bản hoặc dự án OCR bảo chứng cho
`docreconstruct`.

## Những giới hạn cần kiểm tra thủ công

- OCR hoặc Markdown có thể bỏ sót nội dung, nhận sai chính tả, dấu thanh, dấu
  phụ, số, dấu câu, ký hiệu khoa học, toán tử hoặc chữ viết tay.
- Công thức có thể vẫn chỉnh sửa được nhưng sai toán tử, dấu ngoặc, điểm căn
  chỉnh, vị trí giới hạn hoặc chỗ xuống dòng.
- Bảng, cột, thứ tự đọc, phông chữ, khoảng cách, hình minh họa, đầu trang, chân
  trang và cách phân trang có thể khác giữa bản gốc, Microsoft Word và
  LibreOffice.
- Độ tin cậy do nguồn OCR cung cấp và việc vượt qua các bước QA chỉ là thông tin
  tham khảo, không chứng minh nội dung toán học, pháp lý hoặc chuyên môn là đúng.
- Khi Markdown và bản gốc không thống nhất, quy trình kết hợp vẫn lấy Markdown
  làm chuẩn cho câu chữ; dự án không tự đoán hoặc âm thầm sửa nội dung.
- Tài liệu giống bản gốc đến từng điểm ảnh và tài liệu dùng hoàn toàn các đối
  tượng Word có thể chỉnh sửa sâu là hai mục tiêu có thể xung đột. Dự án không
  cam kết tái tạo 1:1 cho mọi tài liệu.

Luôn so sánh tệp đầu ra với bản gốc và nhờ người có chuyên môn kiểm tra trước
khi dùng cho thi cử, lưu trữ hồ sơ, pháp lý, y tế, tài chính, tuân thủ hoặc bất
kỳ công việc quan trọng nào khác.

Xem [README tiếng Anh](../../README.md) để biết đầy đủ về API, các nguồn OCR
được hỗ trợ, kiến trúc, những dự án tham khảo, quyền riêng tư và giấy phép.
