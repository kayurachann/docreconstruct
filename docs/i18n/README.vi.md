# docreconstruct — tài liệu hướng dẫn bằng tiếng Việt

[Tiếng Anh](../../README.md) · **Tiếng Việt** · [Tiếng Trung (giản thể)](README.zh-CN.md) ·
[Tiếng Nga](README.ru.md)

`docreconstruct` tái tạo PDF, bản quét và ảnh chụp tài liệu thành các tệp có cấu
trúc, có thể chỉnh sửa. Trong quy trình này, kết quả OCR chỉ là một nguồn dữ liệu
để đối chiếu chứ không phải sản phẩm cuối cùng. Mô hình tài liệu hợp nhất, trình
lập bố cục và trình xuất tệp sẽ quyết định cách tạo DOCX, HTML hoặc JSON.

## Tình trạng đánh giá chuẩn công khai

Phép thử tái tạo bằng dữ liệu chuẩn (`oracle_reconstruction`) đã chạy **đủ 18
trang** của bộ demo chính thức OmniDocBench. Quy trình dùng Markdown và hình học
chuẩn để tách riêng chất lượng tái tạo, vì vậy **không phải** phép so sánh OCR.

| Kết quả | Giá trị đo được |
| --- | ---: |
| Quy trình chạy thành công sau khi sửa phép chiếu | **18/18 (100%)** |
| Đạt toàn bộ tiêu chí đo được | **2/18 (11,11%)** |
| Thất bại khi căn chỉnh bằng chứng ở chế độ nghiêm ngặt | **0/18** |
| Điểm hình ảnh LibreOffice v2.2, tính đủ 18 trang | **0,214798** |

Mười lỗi cũ bắt nguồn từ việc chiều rộng và chiều cao của trang bị đảo khi
chuyển dữ liệu OmniDocBench sang định dạng nội bộ, chứ không phải do bộ so khớp
chữ gần đúng. Sau khi sửa đúng phép chiếu mà không hạ tiêu chuẩn nghiêm ngặt,
cả 18 trang đều tạo được DOCX; tuy vậy chỉ hai trang vượt mọi tiêu chí. Vì thế
chất lượng của trình lập bố cục và trình dựng tệp vẫn còn yếu. Xem [báo cáo mới
và lỗi của từng trang](../../benchmark/omnidocbench-demo/projection-0.2-metric-2.2/README.md)
cùng [mốc cũ được giữ nguyên để đối chiếu](../../benchmark/omnidocbench-demo/README.md).
Không được so trực tiếp điểm hình ảnh phiên bản 2.1 với 2.2.

Dự án đã có bộ chạy kiểm thử chỉ dùng tài liệu gốc và một lựa chọn Tesseract cục
bộ, miễn phí. Tuy nhiên phép thử 296 trang khó và toàn bộ 1.651 trang vẫn chưa
chạy xong, nên chưa có cơ sở tuyên bố tốt hơn Docling, MinerU hoặc Marker.

## Giao diện web và việc tải tài liệu lên

Sau khi quy trình triển khai GitHub Pages hoàn tất, giao diện web tĩnh sẽ có tại
[kayurachann.github.io/docreconstruct](https://kayurachann.github.io/docreconstruct/).
Đây chỉ là giao diện chạy trong trình duyệt, không phải dịch vụ xử lý tài liệu.
GitHub Pages không thể chạy Python, LibreOffice, Triton, vLLM hoặc mô hình OCR
trên GPU; kho mã nguồn này cũng không cung cấp máy chủ xử lý công cộng hay GPU
miễn phí không giới hạn.

Để sử dụng giao diện, người dùng phải chọn máy chủ do một đơn vị mà mình tin cậy
vận hành. Chế độ chất lượng cao cần đủ tệp Markdown đã rà soát, PDF hoặc ảnh gốc
và JSON có thông tin vị trí. Người dùng có thể tải JSON đã có lên, hoặc chủ động
chọn một dịch vụ OCR trực tuyến để tạo JSON. Trước khi bất kỳ tệp nào rời khỏi
thiết bị, giao diện sẽ nêu rõ nơi nhận và yêu cầu người dùng xác nhận.

Khi thay đổi địa chỉ máy chủ hoặc lựa chọn OCR, người dùng phải xác nhận lại.
Chính sách lưu giữ, quyền riêng tư, nơi xử lý dữ liệu, hạn mức và chi phí đều do
đơn vị vận hành quyết định. Xem thêm [hướng dẫn về hiệu năng và triển khai](../PERFORMANCE.md).

## Bộ đầu vào cho kết quả tốt nhất

Chế độ chất lượng cao chỉ bắt đầu khi có đủ ba loại đầu vào bổ trợ lẫn nhau.
Mỗi loại giữ một vai trò riêng và không thể thay thế hoàn toàn cho hai loại còn
lại:

| Đầu vào | Vai trò trong quá trình tái tạo |
| --- | --- |
| `content.md` đã được rà soát | Nội dung văn bản và thứ tự đọc mong muốn; dự án không tự ý sửa câu chữ hoặc thêm nội dung |
| Một hoặc nhiều tệp `.json` từ dịch vụ OCR hoặc phân tích bố cục | Trang và khối nội dung tương ứng, tọa độ, loại nội dung, bảng, công thức, kiểu trình bày, độ tin cậy và thông tin nguồn gốc |
| PDF hoặc ảnh gốc | Khổ trang thực tế, hình thức trình bày, cột, bảng, hình minh họa và các vùng ảnh cần khôi phục |

Trước khi tổng hợp, dự án chuẩn hóa và đối chiếu riêng từng tệp JSON. JSON có
thể giúp xác định bố cục và cấu trúc, nhưng không được ghi đè câu chữ trong
Markdown. PDF hoặc ảnh gốc vẫn là căn cứ cuối cùng về hình thức và kích thước
trang. Tệp JSON không liên quan hoặc mâu thuẫn sẽ bị từ chối hoặc được nêu rõ
trong báo cáo QA. JSON chỉ chứa văn bản rời rạc là chưa đủ: mỗi khối phải gắn với
trang, kích thước trang và khung hoặc đa giác tọa độ. Nếu thiếu một trong ba
nguồn, dự án vẫn có thể xử lý theo chế độ ước lượng, nhưng kết quả phải được xem
là có độ tin cậy thấp hơn, không phải kết quả chất lượng cao.

### Chưa có JSON vị trí?

Người dùng có thể mở trang chính thức để tự xuất tệp, hoặc dùng tài khoản và
khóa riêng với máy chủ đáng tin cậy. Không được đặt khóa dùng chung trong mã
JavaScript của GitHub Pages. Hạn mức, giá và chính sách có thể thay đổi; hãy đọc
trang chính thức ngay trước khi tải tài liệu lên.

| Lựa chọn | Phù hợp với | Điều cần biết trước khi dùng |
| --- | --- | --- |
| [API chính thức / AI Studio của PaddleOCR](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/overview.html) | Bản quét đa ngôn ngữ, ảnh chụp méo, bảng và công thức; có thể xuất Markdown cùng JSON | Dùng quyền truy cập AI Studio của chính người dùng. [Tài liệu hạn mức hiện tại](https://ai.baidu.com/ai-doc/AISTUDIO/pmjcld5qm) ghi 3.000 trang mỗi ngày cho mỗi mô hình và mỗi người dùng, đồng thời chỉ xử lý 100 trang đầu của tệp dài hơn. Đây không phải cam kết thời gian hoạt động. Tài liệu API được dẫn không nêu thời hạn lưu riêng cho PaddleOCR, vì vậy không nên gửi tài liệu nhạy cảm trước khi đọc chính sách hiện hành của Baidu. |
| [Mistral OCR](https://docs.mistral.ai/api/endpoint/ocr) | Trang phức tạp, Markdown có cấu trúc và dữ liệu vị trí | Cần khóa riêng và tải tệp lên Mistral. [Bảng giá](https://mistral.ai/pricing/api/) tính theo trang; hạn mức thử nghiệm phụ thuộc từng tài khoản, không phải năng lực miễn phí được bảo đảm. [Không lưu dữ liệu](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr) chỉ dành cho gói trả phí đủ điều kiện và không bao phủ mọi cách tải tệp hoặc xử lý theo lô. |
| [Mathpix](https://docs.mathpix.com/) | Công thức toán, tài liệu khoa học và chữ viết tay | [Không có bản dùng thử miễn phí](https://website.mathpix.com/docs/convert/billing) và có phí thiết lập, phí sử dụng. Mathpix yêu cầu [không để khóa bí mật trong mã phía trình duyệt](https://docs.mathpix.com/reference/authentication), nên PDF cần máy chủ đáng tin cậy. [Chính sách lưu giữ](https://docs.mathpix.com/concepts/data-retention) hiện nêu tối đa 30 ngày cho ảnh nguồn và 90 ngày cho văn bản nhận dạng. |
| [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0) | Biểu mẫu, bảng, thứ tự đọc, Markdown và đa giác tọa độ | Cần tài nguyên Azure và thông tin xác thực của người dùng. [Gói F0](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0) hiện cho 500 trang mỗi tháng nhưng chỉ đọc hai trang đầu của mỗi yêu cầu. Microsoft cho biết dữ liệu phân tích được lưu tạm trong vùng đã chọn và [xóa trong vòng 24 giờ](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/faq?view=doc-intel-4.0.0). |
| [Google Document AI](https://docs.cloud.google.com/document-ai/docs/overview) | Nhận dạng tài liệu doanh nghiệp, biểu mẫu và dữ liệu tọa độ chi tiết | Cần dự án Google Cloud, bộ xử lý, thanh toán và quyền OAuth. [Bảng giá](https://cloud.google.com/products/document-ai/pricing) hiện miễn phí 1.000 trang Enterprise Document OCR đầu tiên mỗi tháng cho mỗi tài khoản rồi tính theo mức sử dụng. Google cho biết [không dùng tài liệu và kết quả dự đoán của khách hàng để huấn luyện](https://docs.cloud.google.com/document-ai/docs/security). Dự án phải tự chuyển JSON trả về thành Markdown. |
| [OCR.space](https://ocr.space/ocrapi) | Tệp ngắn, không nhạy cảm, cần gọi trực tiếp từ trình duyệt | Gói miễn phí hiện giới hạn 500 yêu cầu mỗi ngày cho mỗi địa chỉ IP, 25.000 yêu cầu mỗi tháng, 1 MB mỗi tệp và ba trang PDF, không có cam kết thời gian hoạt động. Hãy dùng khóa riêng của người dùng vì khóa trong trình duyệt vẫn có thể bị sao chép và dùng hết hạn mức. Nhà cung cấp cho biết không lưu tệp nguồn và văn bản OCR. |
| [olmOCR](https://github.com/allenai/olmocr) | PDF khó, chữ viết tay, toán, bảng và bố cục nhiều cột | Mã nguồn Apache-2.0 không đồng nghĩa có máy chủ miễn phí. Chạy tại máy cần GPU phù hợp; nhà cung cấp suy luận từ xa tính phí riêng. [Bản trình diễn](https://olmocr.allenai.org/) chỉ để thử, không có API sản xuất hay cam kết thời gian hoạt động được công bố. |
| [Bản trình diễn trên Hugging Face Spaces](https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo) | Thử mô hình trước khi chọn cách triển khai | [ZeroGPU](https://huggingface.co/docs/hub/main/spaces-zerogpu) có số phút GPU mỗi ngày tùy loại tài khoản, có hàng đợi và giới hạn thời gian chạy; [máy chủ suy luận chuyên dụng](https://huggingface.co/docs/inference-endpoints/en/pricing) là dịch vụ trả phí. Không dùng bản trình diễn công cộng làm máy chủ mặc định của dự án. |

Trong dự án, `paddleocr_official` dùng `PADDLEOCR_ACCESS_TOKEN` để gửi công việc
bất đồng bộ tới AI Studio theo [bộ công cụ chính thức](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html).
Nó khác với `paddleocr_vl_server`, vốn trỏ tới máy chủ PaddleOCR-VL do đơn vị vận
hành tự chọn và quản lý. Đơn vị vận hành có thể cho phép công khai một danh sách
nhỏ bằng `DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS`. Điểm
`/v1/hybrid/capabilities` chỉ liệt kê tên trong danh sách cho phép và chỉ đánh
dấu có thể dùng khi máy chủ đã cấu hình đủ; nó không trả về khóa, mã truy cập
hay địa chỉ dịch vụ bí mật.

Theo mặc định, nhãn số trang phải khớp chính xác. Dự án chỉ ghép lại theo thứ tự
khi cả hai dãy trang đều đầy đủ, liên tiếp và có cùng số lượng — chẳng hạn các
trang OCR 5–6 tương ứng với hai trang ảnh cắt được đánh số 1–2. Mỗi lần ghép lại
theo cách này đều được ghi nhận dưới dạng cảnh báo trong báo cáo kiểm tra. Dự án
không suy đoán cách ghép nếu một trong hai dãy trang bị thiếu hoặc không liên
tục. Đơn vị tọa độ và thông tin tiền xử lý của từng nguồn OCR cũng được giữ lại
để có thể quy đổi vị trí về đúng trang gốc.

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
trang gốc trở thành một phần độc lập trong tài liệu Microsoft Word, giữ nguyên
khổ trang tương ứng và luôn bắt đầu ở trang mới. Một nhóm nội dung có thể tiếp
tục sang trang kế tiếp nếu có dữ liệu đối chiếu độc lập xác nhận; trang trắng
hoặc trang bị OCR bỏ sót vẫn được giữ thành một phần trống, thay vì bị lấp bằng
nội dung lấy nhầm từ trang sau. Quy trình QA mặc định kiểm tra số phần tài liệu
đã tạo; khi dùng LibreOffice, quy trình còn kiểm tra số trang kết xuất có đúng
bằng số trang của bản gốc hay không.

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
tra trực quan:

```powershell
docreconstruct hybrid content.md original.png -o output/result.docx `
  --qa-backend libreoffice `
  --min-visual-score 0.80 `
  --qa-report output/result.qa.json
```

## Các ví dụ tái tạo thành công

Đây là những kết quả thực tế do chính quy trình tái tạo kết hợp mà lệnh CLI sử
dụng tạo ra. Ảnh nguồn có độ phân giải đầy đủ, tệp DOCX có thể tải xuống để chỉnh
sửa và ảnh xem trước do dự án kết xuất đều được cung cấp để bạn trực tiếp kiểm
tra kết quả, thay vì chỉ dựa vào ảnh chụp màn hình. Xem thêm [ghi chú về các tệp
minh họa](../showcases/README.md) và [danh sách mã kiểm tra
SHA-256](../showcases/SHA256SUMS.txt) để biết thông tin truy xuất nguồn gốc.

> **Cần tự kiểm chứng:** Kết quả OCR và Markdown do nhà cung cấp xuất ra có thể
> sai chính tả, dấu thanh, ký hiệu, công thức, bảng hoặc thứ tự đọc. Vì Markdown
> là căn cứ về nội dung, tệp DOCX có thể giữ nguyên những lỗi đó. Luôn đối chiếu
> tệp DOCX với tài liệu gốc trước khi sử dụng.

### Đề thi môn Toán của Trường THPT Chuyên Tuyên Quang – Trang 1 – Mã đề 0110 (Nguồn: VietnamNet)

**Nguồn:** [VietnamNet](https://vietnamnet.vn/) — thông tin nguồn do người
đóng góp cung cấp; vui lòng xem phần lưu ý về quyền sử dụng bên dưới.

| Trang chụp gốc | Tệp DOCX có thể chỉnh sửa do dự án kết xuất |
| :---: | :---: |
| [<img src="../showcases/math-exam/source-original.png" alt="Trang gốc của đề thi môn Toán Trường THPT Chuyên Tuyên Quang" width="420">](../showcases/math-exam/source-original.png) | [<img src="../showcases/math-exam/rendered-preview.png" alt="Ảnh xem trước của đề thi môn Toán được kết xuất từ tệp DOCX có thể chỉnh sửa" width="420">](../showcases/math-exam/rendered-preview.png) |

**Tệp đính kèm:** [ảnh gốc](../showcases/math-exam/source-original.png) ·
[tệp DOCX có thể chỉnh sửa](../showcases/math-exam/editable.docx) ·
[ảnh xem trước đã kết xuất](../showcases/math-exam/rendered-preview.png)

Ví dụ này minh họa cách xử lý một trang được chụp bằng máy ảnh, phần đầu trang
có bố cục hỗn hợp, bảng Microsoft Word có thể chỉnh sửa, công thức Office Math,
đáp án trắc nghiệm bốn lựa chọn và việc dùng lại biểu đồ biến thiên từ ảnh
nguồn. Chữ viết tay, biến dạng do góc chụp, phần chữ bị OCR bỏ sót và một số chi
tiết trang trí trong bản gốc không được bảo đảm sẽ tái tạo thành nội dung có thể
chỉnh sửa.

### Các phép biến đổi trong giải tích – công thức Office Math có thể chỉnh sửa (Nguồn: PaddleOCR)

**Nguồn:** [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — thông tin về
nguồn OCR và tệp xuất do người đóng góp cung cấp.

| Tài liệu gốc | Tệp DOCX có thể chỉnh sửa do dự án kết xuất |
| :---: | :---: |
| [<img src="../showcases/calculus-derivation/source-original.jpg" alt="Tài liệu gốc về các phép biến đổi trong giải tích" width="420">](../showcases/calculus-derivation/source-original.jpg) | [<img src="../showcases/calculus-derivation/rendered-preview.png" alt="Ảnh xem trước của các phép biến đổi trong giải tích được kết xuất từ tệp DOCX có thể chỉnh sửa" width="420">](../showcases/calculus-derivation/rendered-preview.png) |

**Tệp đính kèm:** [ảnh gốc](../showcases/calculus-derivation/source-original.jpg) ·
[tệp DOCX có thể chỉnh sửa](../showcases/calculus-derivation/editable.docx) ·
[ảnh xem trước đã kết xuất](../showcases/calculus-derivation/rendered-preview.png)

Ví dụ này minh họa các công thức Office Math có thể chọn và chỉnh sửa trực tiếp,
gồm phân số, tích phân, giới hạn, chỉ số, các bước biến đổi được căn hàng và văn
bản tiếng Trung xen kẽ. Bộ lập bố cục chung ánh xạ 10 khối có thể chỉnh sửa vào
đủ 18 dòng của tài liệu gốc; tệp DOCX hoàn chỉnh được kết xuất thành một trang
A4, giữ 8 biểu thức Office Math và 13 dòng công thức riêng, đồng thời không để lộ
ký hiệu căn chỉnh của TeX. Quy trình QA của dự án vượt qua 34/34 tiêu chí đo được
tại thời điểm tạo tệp showcase; bộ tiêu chí nay đã tăng lên 39. Con số 92,58%
được lưu lại có trước metric hiện hành và không được so sánh trực tiếp: đo lại
bằng `VISUAL_METRIC_VERSION = 2.2`, chính tệp này đạt **31,46%**, vì metric 2.2
chấm mức khớp của vùng tiền cảnh, biên và vùng thay vì khớp pixel thô (độ tương
đồng pixel của nó là 94,71%). Xem bảng đo lại cho cả ba showcase tại
[docs/showcases/README.md](../showcases/README.md). Điểm tương đồng tự động
không chứng minh mọi ký tự hoặc mệnh đề toán học đều chính xác về nghĩa.

### Đề thi Ngữ văn lần thứ hai của Trường THPT Chuyên Tuyên Quang (Nguồn: VNExpress)

**Nguồn:** [VNExpress](https://vnexpress.net/) — thông tin nguồn do người
đóng góp cung cấp; vui lòng xem phần lưu ý về quyền sử dụng bên dưới.

| Tài liệu gốc | Tệp DOCX có thể chỉnh sửa do dự án kết xuất |
| :---: | :---: |
| [<img src="../showcases/vietnamese-exam/source-original.png" alt="Trang gốc của đề thi Ngữ văn lần thứ hai" width="420">](../showcases/vietnamese-exam/source-original.png) | [<img src="../showcases/vietnamese-exam/rendered-preview.png" alt="Ảnh xem trước của đề thi Ngữ văn được kết xuất từ tệp DOCX có thể chỉnh sửa" width="420">](../showcases/vietnamese-exam/rendered-preview.png) |

**Tệp đính kèm:** [ảnh gốc](../showcases/vietnamese-exam/source-original.png) ·
[tệp DOCX có thể chỉnh sửa](../showcases/vietnamese-exam/editable.docx) ·
[ảnh xem trước đã kết xuất](../showcases/vietnamese-exam/rendered-preview.png)

Ví dụ này minh họa phần đầu đề thi được chia thành hai vùng, kiểu chữ có chân
phù hợp với văn bản tiếng Việt, các đoạn văn thụt đầu dòng và căn đều hai bên,
vị trí ghi nguồn trích dẫn, các dòng chấm để điền thông tin thí sinh và câu hỏi
có thể chỉnh sửa. Lỗi chính tả hoặc dấu thanh do OCR vẫn có thể còn sót lại; hình
mờ trên tài liệu gốc, thông tin thí sinh bị che và những chi tiết chỉ tồn tại
dưới dạng điểm ảnh sẽ không tự động được dựng lại thành văn bản có thể chỉnh sửa.

### Quyền sử dụng và quyền riêng tư của các tệp minh họa

Ảnh gốc, ảnh kết xuất từ DOCX, tệp DOCX có thể chỉnh sửa và mã kiểm tra SHA-256
được lưu trong [thư mục tệp minh họa](../showcases/README.md). Thông tin nguồn do
người đóng góp cung cấp chỉ nhằm giúp truy vết tài liệu; thông tin này không phải
là giấy phép tái sử dụng và cũng không có nghĩa đơn vị xuất bản, nguồn OCR hay
`docreconstruct` bảo chứng cho nội dung. Giấy phép Apache-2.0 chỉ áp dụng cho mã
nguồn của dự án, không tự động cấp quyền đối với nội dung đề thi, biểu trưng, hình
mờ, chữ viết tay hoặc tài liệu của bên thứ ba trong các ví dụ. Quyền đối với tài
liệu gốc vẫn thuộc về chủ sở hữu tương ứng. Hãy kiểm tra quyền sử dụng và quyền
riêng tư trước khi phân phối lại hoặc dùng lại bất kỳ tệp minh họa nào.

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
- Mục tiêu tạo ra tài liệu giống bản gốc đến từng điểm ảnh có thể xung đột với
  mục tiêu chỉ dùng các đối tượng Microsoft Word gốc, có thể chỉnh sửa sâu. Dự
  án không cam kết tái tạo 1:1 cho mọi tài liệu.

Luôn so sánh tệp đầu ra với bản gốc và nhờ người có chuyên môn kiểm tra trước
khi dùng cho thi cử, lưu trữ hồ sơ, pháp lý, y tế, tài chính, tuân thủ hoặc bất
kỳ công việc quan trọng nào khác.

Xem [README tiếng Anh](../../README.md) để biết đầy đủ về API, các nguồn OCR
được hỗ trợ, kiến trúc, những dự án tham khảo, quyền riêng tư và giấy phép.
