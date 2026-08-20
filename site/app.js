"use strict";

const translations = {
  vi: {
    pageTitle: "docreconstruct — Biến bản scan thành Word có thể sửa",
    skipLink: "Đi tới biểu mẫu",
    languageLabel: "Ngôn ngữ",
    sourceCodeAria: "Mở mã nguồn trên GitHub",
    sourceCode: "Mã nguồn",
    eyebrow: "Chuẩn bị và gửi tác vụ trên trình duyệt",
    title: "Biến bản scan thành Word có thể sửa",
    subtitle:
      "Dùng đủ bản gốc, tệp chữ và tệp vị trí để giữ nội dung lẫn bố cục tốt nhất có thể. Không cần máy tính mạnh: phần xử lý sẽ chạy ở dịch vụ bạn chọn.",
    factsAria: "Thông tin chính",
    factOne: "Ba tệp giúp kết quả chính xác hơn",
    factTwo: "Word tải về có thể chỉnh sửa",
    factThree: "Bạn biết rõ tệp được gửi đi đâu",
    formStep: "Ba bước đơn giản",
    formTitle: "Tạo tệp Word của bạn",
    noBackendBadge: "Cần đủ 3 tệp",
    onlineInputBadge: "JSON sẽ được tạo",
    serverSection: "Chọn nơi xử lý",
    backendLabel: "Địa chỉ dịch vụ docreconstruct",
    required: "Bắt buộc",
    backendPlaceholder: "https://dich-vu.example",
    backendHelp:
      "Dán địa chỉ do đơn vị cung cấp dịch vụ gửi cho bạn. Trang sẽ tự nối đúng đường dẫn xử lý.",
    noPublicBackendTitle: "Hiện chưa có dịch vụ công cộng mặc định.",
    noPublicBackendBody:
      "GitHub chỉ mở trang web này, không chạy OCR hay Word. Chỉ dùng địa chỉ từ đơn vị bạn tin tưởng.",
    filesSection: "Chuẩn bị đủ ba tệp",
    sourceExplainerTitle: "Vì sao cần ba tệp?",
    sourceExplainerBody:
      "Bản gốc cho biết trang trông thế nào, Markdown giữ chữ và công thức, còn JSON cho biết từng phần nằm ở đâu. Thiếu một tệp có thể làm sai vị trí hoặc thứ tự đọc.",
    originalRole: "1 · Hình dáng trang",
    markdownRole: "2 · Chữ có thể sửa",
    jsonRole: "3 · Vị trí từng phần",
    markdownLabel: "Tệp chữ Markdown (.md)",
    markdownHelp: "Giữ chữ, bảng và công thức",
    chooseFile: "Chọn tệp .md",
    originalLabel: "Bản gốc PDF hoặc ảnh",
    originalHelp: "Giữ khổ giấy, hình ảnh và vị trí",
    chooseOriginal: "Chọn tệp gốc",
    jsonLabel: "Tệp vị trí JSON (.json)",
    jsonHelp: "Giữ cột, khối chữ và thứ tự đọc",
    chooseJson: "Chọn tệp .json",
    optional: "Không bắt buộc",
    bundleWaitingTitle: "Chưa đủ bộ ba tệp",
    bundleWaitingBody: "Hãy chọn bản gốc, Markdown và JSON.",
    bundleOnlineWaitingBody: "Hãy chọn bản gốc và Markdown; dịch vụ OCR sẽ tạo JSON.",
    bundleReadyTitle: "Đã đủ dữ liệu để dựng chính xác hơn",
    bundleReadyBody: "Nội dung, vị trí và bản gốc sẽ được đối chiếu cùng nhau.",
    bundleOnlineReadyBody: "Bản gốc và Markdown đã sẵn sàng; JSON sẽ được tạo trực tuyến.",
    bundleUnsupportedBody: "Dịch vụ OCR đã chọn không đọc được loại tệp gốc này.",
    bundleTooLargeBody: "Tệp {name} vượt quá giới hạn {size} cho mỗi tệp.",
    ocrHelperEyebrow: "Chưa có tệp .md và .json?",
    ocrHelperTitle: "Tạo dữ liệu OCR trước",
    ocrHelperBody:
      "Để có đủ bộ ba, hãy dùng dịch vụ có ghi rõ xuất được cả Markdown và JSON vị trí. Bản demo chỉ để thử khả năng nhận chữ và không thay thế tệp JSON bắt buộc.",
    ocrLinksAria: "Dịch vụ OCR trực tuyến",
    paddleLinkHelp: "Tạo bộ dữ liệu · cần tài khoản",
    paddleDemoHelp: "Chỉ thử OCR/Markdown · không đảm bảo JSON",
    ocrHelperNote:
      "Đây là các trang độc lập. Khả năng xuất JSON, giới hạn miễn phí và thời gian lưu tệp có thể thay đổi; docreconstruct không gửi tệp khi bạn chỉ mở liên kết.",
    optionsSection: "Chọn tốc độ và độ kiểm tra",
    providerLabel: "Tệp JSON được tạo bởi",
    providerAuto: "Để hệ thống tự nhận biết",
    providerHelp: "Không nhớ thì cứ để hệ thống tự nhận biết.",
    qualityLabel: "Bạn ưu tiên điều gì?",
    qualityFast: "Có Word sớm hơn",
    qualityFastHelp: "Bỏ qua bước dựng ảnh kiểm tra",
    qualityVerified: "Kiểm tra kỹ hơn",
    qualityVerifiedHelp: "Chậm hơn vì phải dựng ảnh đối chiếu",
    qualityVerifiedUnavailable: "Dịch vụ xử lý chưa bật lựa chọn này",
    advancedTitle: "Tuỳ chọn cho tài liệu khó",
    advancedIntro: "Tất cả đều hiện sẵn. Nếu chưa chắc, hãy giữ các lựa chọn mặc định.",
    advancedVisibleBadge: "Luôn hiển thị",
    strictLabel: "Báo ngay nếu tệp JSON bị lỗi",
    strictHelp: "Khuyên dùng để tránh tạo Word sai bố cục mà không biết.",
    remoteAssetsLabel: "Lấy cả ảnh được gắn bằng liên kết",
    remoteAssetsHelp:
      "Chỉ bật khi bạn tin các liên kết trong tệp Markdown.",
    remoteAssetsUnavailable: "Dịch vụ xử lý chưa bật lựa chọn này.",
    paddleFallbackLabel: "Cho phép PaddleOCR đọc lại chỗ khó",
    paddleFallbackHelp: "Có thể chậm hơn và gửi bản gốc tới dịch vụ OCR khác.",
    paddleFallbackUnavailable: "Đơn vị xử lý chưa xác nhận đã bật lựa chọn này.",
    outputNameLabel: "Tên tệp đầu ra",
    outputNamePlaceholder: "tai_lieu_chinh_sua_duoc.docx",
    capabilityTitle: "Dịch vụ này có thể làm gì?",
    capabilityIntro:
      "Sau khi bạn nhập địa chỉ, trang sẽ hỏi dịch vụ xem có thể tạo JSON giúp bạn hay không.",
    capabilityWaiting: "Chưa kiểm tra",
    capabilityChecking: "Đang kiểm tra",
    capabilityAvailable: "Đã kết nối",
    capabilityUnavailable: "Chưa kết nối",
    capabilityPrompt: "Nhập địa chỉ dịch vụ để xem lựa chọn OCR trực tuyến.",
    capabilityCheckingMessage: "Đang hỏi dịch vụ về các lựa chọn OCR…",
    capabilityUploadOnly:
      "Dịch vụ này nhận bộ ba tệp có sẵn. Bạn cần tự tải lên JSON cùng bản gốc và Markdown.",
    capabilityOnlineAvailable:
      "Dịch vụ có thể tạo JSON trực tuyến. Bạn có thể dùng JSON sẵn có hoặc chọn một OCR bên dưới.",
    capabilityError:
      "Không đọc được thông tin dịch vụ. Bạn vẫn có thể dùng bộ ba tệp có sẵn nếu địa chỉ này hỗ trợ docreconstruct.",
    capabilityLimit: "Giới hạn mỗi tệp: {size}",
    evidenceModeTitle: "Bạn muốn chuẩn bị JSON bằng cách nào?",
    evidenceUploadTitle: "Tôi đã có tệp JSON",
    evidenceUploadHelp: "Nhanh và ổn định nhất vì không phải chạy OCR lại.",
    evidenceOnlineTitle: "Nhờ OCR trực tuyến tạo JSON",
    evidenceOnlineHelp: "Tiện hơn nhưng có thể phải chờ, có giới hạn hoặc phát sinh phí.",
    evidenceGeneratedBadge: "OCR sẽ tạo",
    onlineProviderLabel: "Dịch vụ OCR sẽ dùng",
    providerPaddleOfficial: "Đám mây chính thức của PaddleOCR",
    providerPaddleCompatible: "Máy chủ tương thích PaddleOCR-VL",
    providerMistral: "Mistral Document AI",
    providerGoogle: "Google Document AI",
    providerAzure: "Azure Document Intelligence",
    providerMathpix: "Mathpix",
    providerDetail: "{privacy} · {cost}",
    providerInputs: "Nhận tệp: {types}",
    providerPrivacyUnknown: "Hãy xem chính sách riêng tư của dịch vụ",
    providerCostUnknown: "Giá và hạn mức do dịch vụ quyết định",
    providerPrivacyLocal: "Tệp không rời dịch vụ xử lý",
    providerPrivacyOperator: "Theo chính sách của đơn vị vận hành",
    providerPrivacyExternal: "Tệp được gửi tới nhà cung cấp OCR",
    providerCostFree: "Không tính phí theo cấu hình hiện tại",
    providerCostQuota: "Có hạn mức miễn phí",
    providerCostPaid: "Có thể phát sinh phí",
    providerCostOperator: "Chi phí do đơn vị vận hành quyết định",
    onlineOptionsTitle: "Nói cho OCR biết tài liệu có gì",
    onlineOptionsIntro:
      "Các lựa chọn giúp mô tả tài liệu và chọn dịch vụ phù hợp. Mức độ chúng thay đổi kết quả tùy từng dịch vụ; không chắc thì để trống.",
    ocrLanguageLabel: "Ngôn ngữ chính",
    ocrLanguageAuto: "Tự nhận biết",
    ocrHandwriting: "Có chữ viết tay",
    ocrFormulas: "Có công thức toán",
    ocrTables: "Có bảng biểu",
    ocrCharts: "Có biểu đồ hoặc sơ đồ",
    ocrDistorted: "Ảnh chụp bị nghiêng hoặc cong",
    ocrDewarping: "Làm phẳng trang trước khi đọc",
    consentTitle: "Tôi đồng ý gửi tệp tới dịch vụ đã chọn",
    consentBody:
      "Các tệp sẽ được gửi tới {backend}. Đơn vị vận hành có thể xử lý hoặc lưu giữ chúng theo chính sách riêng. Nếu dùng OCR trực tuyến hoặc bật PaddleOCR đọc lại, bản gốc có thể được chuyển tiếp tới nhà cung cấp OCR. Khóa truy cập luôn nằm ở máy chủ, không được nhập hay lưu trên trang này.",
    consentBackendUnset: "dịch vụ chưa được chọn",
    submit: "Tạo tệp Word",
    submitHint: "Chọn đủ dữ liệu, nhập nơi xử lý và xác nhận đồng ý để tiếp tục.",
    submitReady: "Mọi thứ đã sẵn sàng. Bạn có thể bắt đầu.",
    sideAria: "Thông tin tác vụ",
    resultStep: "Trạng thái",
    progressReadyTitle: "Sẵn sàng bắt đầu",
    progressReadyMessage: "Chuẩn bị dữ liệu và chọn dịch vụ xử lý để bắt đầu.",
    progressAria: "Tiến độ dựng tài liệu",
    downloadAgain: "Tải lại",
    speedTitle: "Muốn nhận kết quả nhanh hơn?",
    speedBody:
      "Dùng JSON có sẵn và chọn “Có Word sớm hơn”. OCR trực tuyến, nhiều trang hoặc kiểm tra bằng ảnh sẽ cần thêm thời gian; trang sẽ hiện thời gian thực sau khi hoàn tất.",
    privacyTitle: "Tệp có thể rời khỏi thiết bị",
    privacyBody:
      "Trang này không giữ tệp. Khi bạn bắt đầu, tệp đi thẳng tới dịch vụ đã chọn; nếu dùng OCR trực tuyến, bản gốc có thể được gửi tiếp tới nhà cung cấp OCR.",
    costTitle: "Có thể có giới hạn hoặc phí",
    costBody:
      "Phần mềm là mã nguồn mở, nhưng dịch vụ xử lý hoặc OCR có thể yêu cầu tài khoản, giới hạn số trang, xếp hàng hoặc tính phí. Hãy xem điều khoản của nơi bạn chọn.",
    accuracyTitle: "Luôn kiểm tra kết quả",
    accuracyBody:
      "OCR và dựng bố cục có thể sai chính tả, công thức hoặc vị trí. Hãy đối chiếu DOCX với bản gốc trước khi sử dụng.",
    footerText: "Giao diện cộng đồng cho docreconstruct",
    noScript: "Bạn cần bật JavaScript để gửi tài liệu tới máy chủ đã chọn.",
    selectedFile: "Đã chọn: {name} ({size})",
    errorBackendMissing: "Hãy nhập địa chỉ dịch vụ xử lý.",
    errorBackendInvalid: "Địa chỉ dịch vụ không hợp lệ. Hãy nhập đầy đủ, gồm http:// hoặc https://.",
    errorBackendCredentials: "Không đặt tên đăng nhập hoặc mật khẩu trong địa chỉ dịch vụ.",
    errorInsecureBackend:
      "Trang bảo mật này không thể gọi một dịch vụ HTTP bên ngoài. Hãy dùng HTTPS hoặc localhost.",
    errorContentMissing: "Hãy chọn tệp Markdown chứa nội dung.",
    errorLayoutMissing: "Hãy chọn tệp PDF hoặc ảnh gốc.",
    errorEvidenceType: "Tệp dữ liệu bố cục phải có phần mở rộng .json.",
    errorEvidenceMissing: "Hãy chọn tệp JSON hoặc nhờ một dịch vụ OCR trực tuyến tạo giúp.",
    errorOnlineProviderMissing: "Hãy chọn dịch vụ OCR trực tuyến sẽ tạo JSON.",
    errorUnsupportedOriginal:
      "Dịch vụ OCR đã chọn không đọc được loại tệp gốc này. Hãy chọn một định dạng được liệt kê bên dưới dịch vụ OCR.",
    errorFileTooLarge: "Tệp {name} quá lớn. Dịch vụ này nhận tối đa {size} cho mỗi tệp.",
    errorProviderWithoutJson: "Hãy thêm tệp JSON hoặc để nguồn dữ liệu ở chế độ tự nhận diện.",
    errorOutputName: "Tên tệp đầu ra không được chứa dấu gạch chéo.",
    errorConsentRequired: "Bạn phải đồng ý với việc gửi và xử lý tệp trước khi tiếp tục.",
    preparingTitle: "Đang chuẩn bị tệp",
    preparingMessage: "Đang kiểm tra đầu vào trước khi gửi.",
    uploadingTitle: "Đang gửi tới dịch vụ",
    uploadingMessage: "Trình duyệt đang gửi trực tiếp các tệp nguồn.",
    processingTitle: "Dịch vụ đang tạo tài liệu",
    processingFast: "Đang phân tích bố cục và tạo tệp DOCX có thể chỉnh sửa.",
    processingVerified: "Đang tạo DOCX và dựng ảnh để kiểm chứng trực quan.",
    finishedTitle: "Đã dựng xong",
    finishedMessage: "Tệp Word đã sẵn sàng và quá trình tải xuống đã bắt đầu.",
    failedTitle: "Không thể hoàn tất",
    networkError: "Không thể kết nối dịch vụ. Hãy kiểm tra lại địa chỉ hoặc thử lại sau.",
    timeoutError: "Dịch vụ chưa phản hồi trong thời gian cho phép.",
    serverError: "Dịch vụ trả về lỗi {status}.",
    unknownError: "Đã xảy ra lỗi không xác định.",
    resultMeta: "{size} · chế độ {quality}",
    qualityFastResult: "nhanh",
    qualityVerifiedResult: "kiểm chứng",
    visualScore: "độ tương đồng {score}",
    serverDuration: "xử lý trong {seconds} giây",
  },
  en: {
    pageTitle: "docreconstruct — Turn scans into editable Word files",
    skipLink: "Skip to the form",
    languageLabel: "Language",
    sourceCodeAria: "Open the source code on GitHub",
    sourceCode: "Source code",
    eyebrow: "Prepare and send jobs from your browser",
    title: "Turn a scan into an editable Word file",
    subtitle:
      "Use the original, a text file, and a position file together to preserve both content and layout as closely as possible. A service you choose does the heavy processing, so you do not need a powerful computer.",
    factsAria: "Key information",
    factOne: "Three sources improve accuracy",
    factTwo: "The downloaded Word file is editable",
    factThree: "You can see where your files are sent",
    formStep: "Three simple steps",
    formTitle: "Create your Word file",
    noBackendBadge: "Three sources needed",
    onlineInputBadge: "JSON will be created",
    serverSection: "Choose where to process it",
    backendLabel: "docreconstruct service address",
    required: "Required",
    backendPlaceholder: "https://your-service.example",
    backendHelp:
      "Paste the address supplied by your service operator. This page adds the processing path for you.",
    noPublicBackendTitle: "There is no default public service yet.",
    noPublicBackendBody:
      "GitHub hosts this page only; it does not run OCR or Word. Use an address from an operator you trust.",
    filesSection: "Prepare the three sources",
    sourceExplainerTitle: "Why are three sources needed?",
    sourceExplainerBody:
      "The original shows how the page looks, Markdown preserves the words and formulas, and JSON says where each part belongs. Missing one can change positions or reading order.",
    originalRole: "1 · Page appearance",
    markdownRole: "2 · Editable text",
    jsonRole: "3 · Position of each part",
    markdownLabel: "Markdown text file (.md)",
    markdownHelp: "Preserves text, tables, and formulas",
    chooseFile: "Choose a .md file",
    originalLabel: "Original PDF or image",
    originalHelp: "Preserves page size, pictures, and positions",
    chooseOriginal: "Choose the original file",
    jsonLabel: "JSON position file (.json)",
    jsonHelp: "Preserves columns, text blocks, and reading order",
    chooseJson: "Choose a .json file",
    optional: "Optional",
    bundleWaitingTitle: "The three-source set is incomplete",
    bundleWaitingBody: "Choose the original, Markdown, and JSON files.",
    bundleOnlineWaitingBody: "Choose the original and Markdown files; the OCR service will create JSON.",
    bundleReadyTitle: "Enough information for a more accurate result",
    bundleReadyBody: "The content, positions, and original will be checked together.",
    bundleOnlineReadyBody: "The original and Markdown are ready; JSON will be created online.",
    bundleUnsupportedBody: "The selected OCR service cannot read this original file type.",
    bundleTooLargeBody: "{name} is larger than the {size} limit for each file.",
    ocrHelperEyebrow: "Do not have .md and .json yet?",
    ocrHelperTitle: "Create OCR data first",
    ocrHelperBody:
      "To complete the three-source set, use a service that explicitly exports both Markdown and positioned JSON. The demo only lets you try OCR and does not replace the required JSON file.",
    ocrLinksAria: "Online OCR services",
    paddleLinkHelp: "Creates the data set · account required",
    paddleDemoHelp: "OCR/Markdown trial only · JSON not guaranteed",
    ocrHelperNote:
      "These are independent websites. JSON export, free allowances, and retention periods may change; docreconstruct does not send anything when you only open a link.",
    optionsSection: "Choose speed and verification",
    providerLabel: "The JSON file was created by",
    providerAuto: "Let the system detect it",
    providerHelp: "If you do not remember, leave automatic detection selected.",
    qualityLabel: "What matters most to you?",
    qualityFast: "Get Word sooner",
    qualityFastHelp: "Skip the rendered-image check",
    qualityVerified: "Check more carefully",
    qualityVerifiedHelp: "Takes longer because the result is rendered and compared",
    qualityVerifiedUnavailable: "The processing service has not enabled this option",
    advancedTitle: "Options for difficult documents",
    advancedIntro: "Everything is visible here. Keep the defaults if you are unsure.",
    advancedVisibleBadge: "Always visible",
    strictLabel: "Tell me if the JSON file is broken",
    strictHelp: "Recommended, so a bad layout is not created without warning.",
    remoteAssetsLabel: "Include pictures attached as links",
    remoteAssetsHelp:
      "Enable only when you trust the links in the Markdown file.",
    remoteAssetsUnavailable: "The processing service has not enabled this option.",
    paddleFallbackLabel: "Let PaddleOCR reread difficult areas",
    paddleFallbackHelp: "May take longer and send the original to another OCR service.",
    paddleFallbackUnavailable: "The processing service has not confirmed that this option is enabled.",
    outputNameLabel: "Output filename",
    outputNamePlaceholder: "editable_document.docx",
    capabilityTitle: "What can this service do?",
    capabilityIntro:
      "After you enter the address, this page asks whether the service can create JSON for you.",
    capabilityWaiting: "Not checked",
    capabilityChecking: "Checking",
    capabilityAvailable: "Connected",
    capabilityUnavailable: "Not connected",
    capabilityPrompt: "Enter a service address to see its online OCR choices.",
    capabilityCheckingMessage: "Asking the service about its OCR choices…",
    capabilityUploadOnly:
      "This service accepts a prepared three-source set. Upload JSON together with the original and Markdown.",
    capabilityOnlineAvailable:
      "This service can create JSON online. Use an existing JSON file or choose an OCR service below.",
    capabilityError:
      "The service information could not be read. You can still use a prepared three-source set if this address supports docreconstruct.",
    capabilityLimit: "Maximum per file: {size}",
    evidenceModeTitle: "How would you like to prepare JSON?",
    evidenceUploadTitle: "I already have a JSON file",
    evidenceUploadHelp: "Fastest and most stable because OCR does not run again.",
    evidenceOnlineTitle: "Ask online OCR to create JSON",
    evidenceOnlineHelp: "More convenient, but there may be a queue, a quota, or a fee.",
    evidenceGeneratedBadge: "OCR will create it",
    onlineProviderLabel: "OCR service to use",
    providerPaddleOfficial: "PaddleOCR official cloud",
    providerPaddleCompatible: "PaddleOCR-VL compatible server",
    providerMistral: "Mistral Document AI",
    providerGoogle: "Google Document AI",
    providerAzure: "Azure Document Intelligence",
    providerMathpix: "Mathpix",
    providerDetail: "{privacy} · {cost}",
    providerInputs: "Accepts: {types}",
    providerPrivacyUnknown: "Read the service's privacy policy",
    providerCostUnknown: "The service controls prices and quotas",
    providerPrivacyLocal: "Files stay within the processing service",
    providerPrivacyOperator: "Covered by the operator's policy",
    providerPrivacyExternal: "Files are sent to the OCR provider",
    providerCostFree: "No charge under the current configuration",
    providerCostQuota: "A free allowance applies",
    providerCostPaid: "Charges may apply",
    providerCostOperator: "The operator sets the cost",
    onlineOptionsTitle: "Tell OCR what is in the document",
    onlineOptionsIntro:
      "These choices describe the document and help select a suitable service. Their effect varies by provider; leave them blank if unsure.",
    ocrLanguageLabel: "Main language",
    ocrLanguageAuto: "Detect automatically",
    ocrHandwriting: "Contains handwriting",
    ocrFormulas: "Contains maths formulas",
    ocrTables: "Contains tables",
    ocrCharts: "Contains charts or diagrams",
    ocrDistorted: "The photo is tilted or curved",
    ocrDewarping: "Flatten the page before reading",
    consentTitle: "I agree to send the files to the selected service",
    consentBody:
      "The files will be sent to {backend}. Its operator may process or retain them under its own policy. If you use online OCR or let PaddleOCR reread the document, the original may be forwarded to an OCR provider. Access keys stay on the server; this page never asks for or stores them.",
    consentBackendUnset: "a service that has not yet been selected",
    submit: "Create Word file",
    submitHint: "Add the required information, enter a processing service, and confirm your consent.",
    submitReady: "Everything is ready. You can start now.",
    sideAria: "Job information",
    resultStep: "Status",
    progressReadyTitle: "Ready to begin",
    progressReadyMessage: "Prepare the sources and choose a processing service to begin.",
    progressAria: "Document reconstruction progress",
    downloadAgain: "Download again",
    speedTitle: "Want the result sooner?",
    speedBody:
      "Use an existing JSON file and choose “Get Word sooner”. Online OCR, many pages, or rendered-image verification will take longer; the actual processing time is shown when the job finishes.",
    privacyTitle: "Files may leave your device",
    privacyBody:
      "This page does not keep your files. When you start, they go directly to the selected service; with online OCR, the original may then be sent to the OCR provider.",
    costTitle: "Limits or charges may apply",
    costBody:
      "The software is open source, but processing and OCR services may require an account, limit pages, use a queue, or charge a fee. Read the terms of the service you choose.",
    accuracyTitle: "Always review the result",
    accuracyBody:
      "OCR and layout reconstruction can introduce spelling, formula, or positioning errors. Compare the DOCX with the original before relying on it.",
    footerText: "Community web client for docreconstruct",
    noScript: "Enable JavaScript to send documents to the server you choose.",
    selectedFile: "Selected: {name} ({size})",
    errorBackendMissing: "Enter the address of a processing service.",
    errorBackendInvalid: "The service address is invalid. Include http:// or https://.",
    errorBackendCredentials: "Do not put a username or password in the service address.",
    errorInsecureBackend:
      "This secure page cannot call an external HTTP service. Use HTTPS or localhost.",
    errorContentMissing: "Choose the Markdown file that contains the content.",
    errorLayoutMissing: "Choose the original PDF or image.",
    errorEvidenceType: "Layout evidence must be a .json file.",
    errorEvidenceMissing: "Choose a JSON file or ask an online OCR service to create one.",
    errorOnlineProviderMissing: "Choose the online OCR service that will create JSON.",
    errorUnsupportedOriginal:
      "The selected OCR service cannot read this original file type. Choose one of the formats listed below the OCR service.",
    errorFileTooLarge: "{name} is too large. This service accepts up to {size} per file.",
    errorProviderWithoutJson: "Add a JSON file or leave the evidence source on automatic detection.",
    errorOutputName: "The output filename cannot contain a slash.",
    errorConsentRequired: "You must agree to the file upload and processing terms before continuing.",
    preparingTitle: "Preparing files",
    preparingMessage: "Checking the inputs before upload.",
    uploadingTitle: "Sending files to the service",
    uploadingMessage: "Your browser is sending the source files directly.",
    processingTitle: "The service is creating the document",
    processingFast: "Analysing the layout and creating an editable DOCX.",
    processingVerified: "Creating the DOCX and rendering it for visual verification.",
    finishedTitle: "Reconstruction complete",
    finishedMessage: "The Word document is ready, and the download has started.",
    failedTitle: "Could not complete the job",
    networkError: "Could not reach the service. Check the address or try again later.",
    timeoutError: "The service did not respond within the allowed time.",
    serverError: "The service returned error {status}.",
    unknownError: "An unknown error occurred.",
    resultMeta: "{size} · {quality} mode",
    qualityFastResult: "fast",
    qualityVerifiedResult: "verified",
    visualScore: "visual similarity {score}",
    serverDuration: "processed in {seconds} seconds",
  },
  "zh-CN": {
    pageTitle: "docreconstruct — 把扫描件变成可编辑的 Word 文档",
    skipLink: "跳转到表单",
    languageLabel: "语言",
    sourceCodeAria: "在 GitHub 上查看源代码",
    sourceCode: "源代码",
    eyebrow: "在浏览器中准备并提交任务",
    title: "把扫描件变成可编辑的 Word 文档",
    subtitle:
      "同时使用原件、文字文件和位置文件，尽可能保留内容与版式。繁重的处理由您选择的服务完成，不需要高性能电脑。",
    factsAria: "重要说明",
    factOne: "三份来源资料有助于提高准确度",
    factTwo: "下载的 Word 文档可以编辑",
    factThree: "文件发送到哪里一目了然",
    formStep: "简单三步",
    formTitle: "创建您的 Word 文档",
    noBackendBadge: "需要三份来源资料",
    onlineInputBadge: "将自动生成 JSON",
    serverSection: "选择处理位置",
    backendLabel: "docreconstruct 服务地址",
    required: "必填",
    backendPlaceholder: "https://您的服务.example",
    backendHelp: "粘贴服务运营方提供的地址，本页面会自动补上正确的处理路径。",
    noPublicBackendTitle: "目前还没有默认的公共服务。",
    noPublicBackendBody: "GitHub 只托管本网页，不运行文字识别或 Word。请只使用您信任的服务地址。",
    filesSection: "准备三份来源资料",
    sourceExplainerTitle: "为什么需要三份资料？",
    sourceExplainerBody:
      "原件说明页面长什么样，Markdown 保留文字和公式，JSON 则记录各部分的位置。缺少其中一份，位置或阅读顺序就可能出错。",
    originalRole: "1 · 页面外观",
    markdownRole: "2 · 可编辑文字",
    jsonRole: "3 · 各部分位置",
    markdownLabel: "Markdown 文字文件（.md）",
    markdownHelp: "保留文字、表格和公式",
    chooseFile: "选择 .md 文件",
    originalLabel: "原始 PDF 或图片",
    originalHelp: "保留纸张大小、图片和位置",
    chooseOriginal: "选择原始文件",
    jsonLabel: "JSON 位置文件（.json）",
    jsonHelp: "保留分栏、文字块和阅读顺序",
    chooseJson: "选择 .json 文件",
    optional: "选填",
    bundleWaitingTitle: "三份资料还不齐全",
    bundleWaitingBody: "请选择原件、Markdown 和 JSON 文件。",
    bundleOnlineWaitingBody: "请选择原件和 Markdown；文字识别服务将生成 JSON。",
    bundleReadyTitle: "资料齐全，可以获得更准确的结果",
    bundleReadyBody: "系统会一并核对内容、位置和原件。",
    bundleOnlineReadyBody: "原件和 Markdown 已就绪；JSON 将在线生成。",
    bundleUnsupportedBody: "所选文字识别服务无法读取这种原始文件。",
    bundleTooLargeBody: "文件 {name} 超过了每个文件 {size} 的上限。",
    ocrHelperEyebrow: "还没有 .md 和 .json 文件？",
    ocrHelperTitle: "先生成文字识别数据",
    ocrHelperBody:
      "要补齐三份资料，请使用明确支持同时导出 Markdown 和带位置 JSON 的服务。下方演示只用于试用文字识别，不能代替必需的 JSON 文件。",
    ocrLinksAria: "在线文字识别服务",
    paddleLinkHelp: "生成整套数据 · 需要账户",
    paddleDemoHelp: "仅试用识别/Markdown · 不保证 JSON",
    ocrHelperNote:
      "这些网站独立运营。JSON 导出、免费额度和文件保留时间可能变化；仅打开链接时，docreconstruct 不会发送任何文件。",
    optionsSection: "选择速度和检查方式",
    providerLabel: "JSON 文件由以下工具生成",
    providerAuto: "让系统自动判断",
    providerHelp: "如果记不清，请保留自动判断。",
    qualityLabel: "您更看重哪一点？",
    qualityFast: "更快拿到 Word",
    qualityFastHelp: "跳过渲染图片检查",
    qualityVerified: "更仔细地检查",
    qualityVerifiedHelp: "需要渲染并比对结果，因此耗时更长",
    qualityVerifiedUnavailable: "处理服务尚未启用此选项",
    advancedTitle: "复杂文档选项",
    advancedIntro: "所有选项都直接显示。不确定时请保留默认设置。",
    advancedVisibleBadge: "始终显示",
    strictLabel: "JSON 文件有问题时立即提醒",
    strictHelp: "建议开启，避免在不知情的情况下生成错误版式。",
    remoteAssetsLabel: "包含通过链接插入的图片",
    remoteAssetsHelp: "只有在信任 Markdown 文件中的链接时才开启。",
    remoteAssetsUnavailable: "处理服务尚未启用此选项。",
    paddleFallbackLabel: "让 PaddleOCR 重新识别困难区域",
    paddleFallbackHelp: "可能需要更长时间，并把原件发送给另一项文字识别服务。",
    paddleFallbackUnavailable: "处理服务尚未确认已启用此选项。",
    outputNameLabel: "输出文件名",
    outputNamePlaceholder: "可编辑文档.docx",
    capabilityTitle: "这项服务能做什么？",
    capabilityIntro: "输入地址后，本页面会询问该服务能否为您生成 JSON。",
    capabilityWaiting: "尚未检查",
    capabilityChecking: "正在检查",
    capabilityAvailable: "已连接",
    capabilityUnavailable: "未连接",
    capabilityPrompt: "输入服务地址即可查看在线文字识别选项。",
    capabilityCheckingMessage: "正在查询服务提供的文字识别选项……",
    capabilityUploadOnly: "这项服务接收准备好的三份资料。请同时上传原件、Markdown 和 JSON。",
    capabilityOnlineAvailable: "这项服务可以在线生成 JSON。您可以上传现有 JSON，也可以在下方选择文字识别服务。",
    capabilityError: "无法读取服务信息。如果该地址支持 docreconstruct，您仍可使用准备好的三份资料。",
    capabilityLimit: "每个文件上限：{size}",
    evidenceModeTitle: "您想怎样准备 JSON？",
    evidenceUploadTitle: "我已经有 JSON 文件",
    evidenceUploadHelp: "无需再次识别，速度最快也最稳定。",
    evidenceOnlineTitle: "请在线文字识别服务生成 JSON",
    evidenceOnlineHelp: "更方便，但可能需要排队、受额度限制或付费。",
    evidenceGeneratedBadge: "由文字识别生成",
    onlineProviderLabel: "要使用的文字识别服务",
    providerPaddleOfficial: "PaddleOCR 官方云服务",
    providerPaddleCompatible: "兼容 PaddleOCR-VL 的服务器",
    providerMistral: "Mistral 文档智能服务",
    providerGoogle: "Google 文档智能服务",
    providerAzure: "Azure 文档智能服务",
    providerMathpix: "Mathpix",
    providerDetail: "{privacy} · {cost}",
    providerInputs: "支持的文件：{types}",
    providerPrivacyUnknown: "请阅读该服务的隐私政策",
    providerCostUnknown: "价格和额度由服务方决定",
    providerPrivacyLocal: "文件不会离开处理服务",
    providerPrivacyOperator: "按运营方的政策处理",
    providerPrivacyExternal: "文件会发送给文字识别提供商",
    providerCostFree: "当前配置不收费",
    providerCostQuota: "提供一定的免费额度",
    providerCostPaid: "可能产生费用",
    providerCostOperator: "费用由运营方决定",
    onlineOptionsTitle: "告诉文字识别服务文档里有什么",
    onlineOptionsIntro: "这些选项用于描述文档并帮助选择合适的服务；实际作用因提供商而异。不确定时可全部留空。",
    ocrLanguageLabel: "主要语言",
    ocrLanguageAuto: "自动判断",
    ocrHandwriting: "含有手写文字",
    ocrFormulas: "含有数学公式",
    ocrTables: "含有表格",
    ocrCharts: "含有图表或示意图",
    ocrDistorted: "照片倾斜或页面弯曲",
    ocrDewarping: "识别前先校正页面",
    consentTitle: "我同意将文件发送到所选服务",
    consentBody:
      "文件将发送至{backend}。运营方可能按照自己的政策处理或保留文件。若使用在线文字识别或让 PaddleOCR 重新识别，原件可能被转交给文字识别提供商。访问密钥始终保存在服务器端，本页面不会要求输入或保存密钥。",
    consentBackendUnset: "尚未选择的服务",
    submit: "创建 Word 文档",
    submitHint: "请补齐所需资料、输入处理服务地址并确认同意。",
    submitReady: "一切准备就绪，可以开始。",
    sideAria: "任务信息",
    resultStep: "状态",
    progressReadyTitle: "可以开始",
    progressReadyMessage: "准备来源资料并选择处理服务即可开始。",
    progressAria: "文档重建进度",
    downloadAgain: "再次下载",
    speedTitle: "想更快拿到结果？",
    speedBody:
      "请使用现有 JSON 文件并选择“更快拿到 Word”。在线文字识别、页数较多或渲染图片检查都需要更多时间；完成后页面会显示实际处理用时。",
    privacyTitle: "文件可能离开您的设备",
    privacyBody:
      "本页面不会保存文件。开始处理后，文件会直接发送到所选服务；如使用在线文字识别，原件还可能被转交给文字识别提供商。",
    costTitle: "可能存在额度限制或费用",
    costBody:
      "本软件是开源的，但处理和文字识别服务可能要求账户、限制页数、需要排队或收取费用。请阅读所选服务的条款。",
    accuracyTitle: "请务必复核结果",
    accuracyBody: "OCR 与版面重建可能出现文字、公式或位置错误。使用前请将 DOCX 与原件逐项核对。",
    footerText: "docreconstruct 社区网页客户端",
    noScript: "请启用 JavaScript，才能把文档发送到您选择的服务器。",
    selectedFile: "已选择：{name}（{size}）",
    errorBackendMissing: "请输入处理服务的地址。",
    errorBackendInvalid: "服务地址无效，请填写包含 http:// 或 https:// 的完整地址。",
    errorBackendCredentials: "请勿在服务地址中填写用户名或密码。",
    errorInsecureBackend: "安全网页无法调用外部 HTTP 服务。请使用 HTTPS 或本机 localhost。",
    errorContentMissing: "请选择包含正文的 Markdown 文件。",
    errorLayoutMissing: "请选择原始 PDF 或图片。",
    errorEvidenceType: "版面数据必须是 .json 文件。",
    errorEvidenceMissing: "请选择 JSON 文件，或让在线文字识别服务为您生成。",
    errorOnlineProviderMissing: "请选择要生成 JSON 的在线文字识别服务。",
    errorUnsupportedOriginal: "所选文字识别服务无法读取这种原始文件。请选择服务说明下列出的格式。",
    errorFileTooLarge: "文件 {name} 过大。此服务每个文件最多接收 {size}。",
    errorProviderWithoutJson: "请添加 JSON 文件，或将版面数据来源设为自动识别。",
    errorOutputName: "输出文件名不能包含斜杠。",
    errorConsentRequired: "继续操作前，您必须同意上传和处理文件。",
    preparingTitle: "正在准备文件",
    preparingMessage: "上传前正在检查输入内容。",
    uploadingTitle: "正在发送到服务",
    uploadingMessage: "浏览器正在直接发送源文件。",
    processingTitle: "服务正在创建文档",
    processingFast: "正在分析版面并生成可编辑的 DOCX。",
    processingVerified: "正在生成 DOCX，并渲染成图进行视觉校验。",
    finishedTitle: "重建完成",
    finishedMessage: "Word 文档已就绪，下载已经开始。",
    failedTitle: "任务未能完成",
    networkError: "无法连接服务，请检查地址或稍后重试。",
    timeoutError: "服务未在规定时间内响应。",
    serverError: "服务返回错误 {status}。",
    unknownError: "发生未知错误。",
    resultMeta: "{size} · {quality}模式",
    qualityFastResult: "快速",
    qualityVerifiedResult: "视觉校验",
    visualScore: "视觉相似度 {score}",
    serverDuration: "处理耗时 {seconds} 秒",
  },
  ru: {
    pageTitle: "docreconstruct — Превратите скан в редактируемый документ Word",
    skipLink: "Перейти к форме",
    languageLabel: "Язык",
    sourceCodeAria: "Открыть исходный код на GitHub",
    sourceCode: "Исходный код",
    eyebrow: "Подготовка и отправка заданий из браузера",
    title: "Превратите скан в редактируемый документ Word",
    subtitle:
      "Используйте вместе оригинал, файл с текстом и файл с расположением элементов, чтобы точнее сохранить содержание и макет. Тяжёлую обработку выполнит выбранная вами служба — мощный компьютер не нужен.",
    factsAria: "Основная информация",
    factOne: "Три источника повышают точность",
    factTwo: "Скачанный документ Word можно редактировать",
    factThree: "Вы всегда видите, куда отправляются файлы",
    formStep: "Три простых шага",
    formTitle: "Создайте документ Word",
    noBackendBadge: "Нужны три источника",
    onlineInputBadge: "JSON будет создан",
    serverSection: "Выберите место обработки",
    backendLabel: "Адрес службы docreconstruct",
    required: "Обязательно",
    backendPlaceholder: "https://ваша-служба.example",
    backendHelp:
      "Вставьте адрес, который выдал оператор службы. Страница сама добавит нужный путь обработки.",
    noPublicBackendTitle: "Общедоступная служба по умолчанию пока не предоставляется.",
    noPublicBackendBody:
      "GitHub только показывает эту страницу и не запускает распознавание или Word. Используйте адрес оператора, которому доверяете.",
    filesSection: "Подготовьте три источника",
    sourceExplainerTitle: "Зачем нужны три источника?",
    sourceExplainerBody:
      "Оригинал показывает внешний вид страницы, Markdown сохраняет текст и формулы, а JSON указывает положение каждого элемента. Без одного из них могут измениться позиции или порядок чтения.",
    originalRole: "1 · Внешний вид страницы",
    markdownRole: "2 · Редактируемый текст",
    jsonRole: "3 · Положение элементов",
    markdownLabel: "Текстовый файл Markdown (.md)",
    markdownHelp: "Сохраняет текст, таблицы и формулы",
    chooseFile: "Выбрать файл .md",
    originalLabel: "Исходный PDF или изображение",
    originalHelp: "Сохраняет размер страницы, изображения и позиции",
    chooseOriginal: "Выбрать оригинал",
    jsonLabel: "Файл расположения JSON (.json)",
    jsonHelp: "Сохраняет колонки, блоки текста и порядок чтения",
    chooseJson: "Выбрать файл .json",
    optional: "Необязательно",
    bundleWaitingTitle: "Не хватает одного или нескольких источников",
    bundleWaitingBody: "Выберите оригинал, Markdown и JSON.",
    bundleOnlineWaitingBody: "Выберите оригинал и Markdown; служба распознавания создаст JSON.",
    bundleReadyTitle: "Данных достаточно для более точного результата",
    bundleReadyBody: "Содержание, позиции и оригинал будут сверены вместе.",
    bundleOnlineReadyBody: "Оригинал и Markdown готовы; JSON будет создан через интернет.",
    bundleUnsupportedBody: "Выбранная служба распознавания не читает этот тип исходного файла.",
    bundleTooLargeBody: "Файл {name} превышает ограничение {size} на один файл.",
    ocrHelperEyebrow: "Ещё нет файлов .md и .json?",
    ocrHelperTitle: "Сначала создайте данные распознавания",
    ocrHelperBody:
      "Чтобы получить полный набор из трёх источников, используйте службу, которая явно экспортирует и Markdown, и JSON с позициями. Демонстрационная версия служит только для пробы распознавания и не заменяет обязательный JSON.",
    ocrLinksAria: "Службы распознавания через интернет",
    paddleLinkHelp: "Создаёт полный набор · нужна учётная запись",
    paddleDemoHelp: "Только проба распознавания/Markdown · JSON не гарантирован",
    ocrHelperNote:
      "Это независимые сайты. Экспорт JSON, бесплатные лимиты и сроки хранения могут меняться; при простом открытии ссылки docreconstruct ничего не отправляет.",
    optionsSection: "Выберите скорость и тщательность проверки",
    providerLabel: "Файл JSON создан программой",
    providerAuto: "Определить автоматически",
    providerHelp: "Если не помните, оставьте автоматическое определение.",
    qualityLabel: "Что для вас важнее?",
    qualityFast: "Быстрее получить Word",
    qualityFastHelp: "Не создавать изображение для проверки",
    qualityVerified: "Проверить тщательнее",
    qualityVerifiedHelp: "Дольше, потому что результат нужно отрисовать и сравнить",
    qualityVerifiedUnavailable: "Служба обработки не включила этот вариант",
    advancedTitle: "Параметры для сложных документов",
    advancedIntro: "Все параметры видны сразу. Если сомневаетесь, оставьте значения по умолчанию.",
    advancedVisibleBadge: "Всегда показаны",
    strictLabel: "Сообщить, если файл JSON повреждён",
    strictHelp: "Рекомендуется, чтобы ошибочный макет не был создан без предупреждения.",
    remoteAssetsLabel: "Добавить изображения, вставленные по ссылкам",
    remoteAssetsHelp: "Включайте только тогда, когда доверяете ссылкам в файле Markdown.",
    remoteAssetsUnavailable: "Служба обработки не включила этот вариант.",
    paddleFallbackLabel: "Попросить PaddleOCR перечитать сложные места",
    paddleFallbackHelp: "Может занять больше времени и отправить оригинал другой службе распознавания.",
    paddleFallbackUnavailable: "Служба обработки не подтвердила, что этот вариант включён.",
    outputNameLabel: "Имя выходного файла",
    outputNamePlaceholder: "редактируемый_документ.docx",
    capabilityTitle: "Что умеет эта служба?",
    capabilityIntro: "После ввода адреса страница узнает, может ли служба создать JSON за вас.",
    capabilityWaiting: "Не проверено",
    capabilityChecking: "Проверяем",
    capabilityAvailable: "Подключено",
    capabilityUnavailable: "Нет подключения",
    capabilityPrompt: "Введите адрес службы, чтобы увидеть доступные варианты распознавания.",
    capabilityCheckingMessage: "Узнаём, какие варианты распознавания предлагает служба…",
    capabilityUploadOnly:
      "Эта служба принимает готовый набор из трёх источников. Загрузите JSON вместе с оригиналом и Markdown.",
    capabilityOnlineAvailable:
      "Эта служба может создать JSON через интернет. Загрузите готовый JSON или выберите службу распознавания ниже.",
    capabilityError:
      "Не удалось прочитать сведения о службе. Если этот адрес поддерживает docreconstruct, можно использовать готовый набор из трёх источников.",
    capabilityLimit: "Ограничение на один файл: {size}",
    evidenceModeTitle: "Как вы хотите подготовить JSON?",
    evidenceUploadTitle: "У меня уже есть файл JSON",
    evidenceUploadHelp: "Самый быстрый и стабильный способ: повторное распознавание не требуется.",
    evidenceOnlineTitle: "Создать JSON в службе распознавания",
    evidenceOnlineHelp: "Удобнее, но возможны очередь, ограничения или плата.",
    evidenceGeneratedBadge: "Создаст служба",
    onlineProviderLabel: "Служба распознавания",
    providerPaddleOfficial: "Официальная облачная служба PaddleOCR",
    providerPaddleCompatible: "Сервер, совместимый с PaddleOCR-VL",
    providerMistral: "Служба документов Mistral",
    providerGoogle: "Служба документов Google",
    providerAzure: "Служба документов Azure",
    providerMathpix: "Mathpix",
    providerDetail: "{privacy} · {cost}",
    providerInputs: "Принимает файлы: {types}",
    providerPrivacyUnknown: "Прочитайте правила конфиденциальности службы",
    providerCostUnknown: "Цены и лимиты задаёт поставщик службы",
    providerPrivacyLocal: "Файлы остаются внутри службы обработки",
    providerPrivacyOperator: "Действуют правила оператора",
    providerPrivacyExternal: "Файлы отправляются поставщику распознавания",
    providerCostFree: "В текущей конфигурации плата не взимается",
    providerCostQuota: "Предоставляется бесплатный лимит",
    providerCostPaid: "Возможна плата",
    providerCostOperator: "Стоимость устанавливает оператор",
    onlineOptionsTitle: "Подскажите службе, что есть в документе",
    onlineOptionsIntro:
      "Эти параметры описывают документ и помогают выбрать подходящую службу. Их влияние зависит от поставщика; если не уверены, ничего не отмечайте.",
    ocrLanguageLabel: "Основной язык",
    ocrLanguageAuto: "Определить автоматически",
    ocrHandwriting: "Есть рукописный текст",
    ocrFormulas: "Есть математические формулы",
    ocrTables: "Есть таблицы",
    ocrCharts: "Есть графики или схемы",
    ocrDistorted: "Фотография наклонена или страница изогнута",
    ocrDewarping: "Выровнять страницу перед распознаванием",
    consentTitle: "Я согласен отправить файлы в выбранную службу",
    consentBody:
      "Файлы будут отправлены по адресу {backend}. Оператор может обрабатывать или хранить их по своим правилам. При распознавании через интернет или повторном чтении с PaddleOCR оригинал может быть передан поставщику распознавания. Ключи доступа остаются на сервере: эта страница не просит и не сохраняет их.",
    consentBackendUnset: "служба ещё не выбрана",
    submit: "Создать документ Word",
    submitHint: "Добавьте нужные данные, укажите службу обработки и подтвердите согласие.",
    submitReady: "Всё готово. Можно начинать.",
    sideAria: "Сведения о задании",
    resultStep: "Состояние",
    progressReadyTitle: "Всё готово",
    progressReadyMessage: "Подготовьте исходные данные и выберите службу обработки.",
    progressAria: "Ход восстановления документа",
    downloadAgain: "Скачать ещё раз",
    speedTitle: "Хотите получить результат быстрее?",
    speedBody:
      "Используйте готовый JSON и выберите «Быстрее получить Word». Распознавание через интернет, большое число страниц и проверка по изображению требуют больше времени; после завершения страница покажет фактическую длительность.",
    privacyTitle: "Файлы могут покинуть ваше устройство",
    privacyBody:
      "Эта страница не хранит файлы. После запуска они отправляются прямо в выбранную службу; при распознавании через интернет оригинал может быть передан поставщику распознавания.",
    costTitle: "Возможны ограничения или плата",
    costBody:
      "Программа имеет открытый исходный код, но службы обработки и распознавания могут требовать учётную запись, ограничивать число страниц, использовать очередь или взимать плату. Прочитайте условия выбранной службы.",
    accuracyTitle: "Обязательно проверьте результат",
    accuracyBody:
      "При распознавании и восстановлении макета возможны ошибки в тексте, формулах и расположении. Перед использованием сравните DOCX с оригиналом.",
    footerText: "Веб-клиент сообщества docreconstruct",
    noScript: "Включите JavaScript, чтобы отправить документ на выбранный сервер.",
    selectedFile: "Выбран файл: {name} ({size})",
    errorBackendMissing: "Введите адрес службы обработки.",
    errorBackendInvalid: "Адрес службы некорректен. Укажите полный адрес с http:// или https://.",
    errorBackendCredentials: "Не указывайте имя пользователя или пароль в адресе службы.",
    errorInsecureBackend:
      "Защищённая страница не может обратиться к внешней службе HTTP. Используйте HTTPS или localhost.",
    errorContentMissing: "Выберите файл Markdown с содержимым документа.",
    errorLayoutMissing: "Выберите исходный PDF или изображение.",
    errorEvidenceType: "Данные о макете должны быть в файле .json.",
    errorEvidenceMissing: "Выберите файл JSON или попросите службу распознавания создать его.",
    errorOnlineProviderMissing: "Выберите службу распознавания, которая создаст JSON.",
    errorUnsupportedOriginal:
      "Выбранная служба распознавания не читает этот тип исходного файла. Выберите один из форматов, указанных под названием службы.",
    errorFileTooLarge: "Файл {name} слишком велик. Эта служба принимает до {size} на один файл.",
    errorProviderWithoutJson:
      "Добавьте файл JSON или оставьте автоматическое определение источника данных.",
    errorOutputName: "Имя выходного файла не должно содержать косую черту.",
    errorConsentRequired:
      "Перед продолжением необходимо согласиться на отправку и обработку файлов.",
    preparingTitle: "Подготовка файлов",
    preparingMessage: "Проверяем исходные данные перед отправкой.",
    uploadingTitle: "Отправка в службу",
    uploadingMessage: "Браузер напрямую отправляет исходные файлы.",
    processingTitle: "Служба создаёт документ",
    processingFast: "Анализируем макет и создаём редактируемый DOCX.",
    processingVerified: "Создаём DOCX и отрисовываем его для визуальной проверки.",
    finishedTitle: "Документ восстановлен",
    finishedMessage: "Документ Word готов; скачивание уже началось.",
    failedTitle: "Не удалось выполнить задание",
    networkError: "Не удалось связаться со службой. Проверьте адрес или повторите попытку позже.",
    timeoutError: "Служба не ответила за отведённое время.",
    serverError: "Служба вернула ошибку {status}.",
    unknownError: "Произошла неизвестная ошибка.",
    resultMeta: "{size} · режим «{quality}»",
    qualityFastResult: "быстро",
    qualityVerifiedResult: "с проверкой",
    visualScore: "визуальное сходство {score}",
    serverDuration: "обработано за {seconds} с",
  },
};

const supportedLanguages = Object.keys(translations);
const elements = {
  language: document.querySelector("#language"),
  form: document.querySelector("#reconstruct-form"),
  backend: document.querySelector("#backend-url"),
  content: document.querySelector("#content-file"),
  layout: document.querySelector("#layout-file"),
  evidence: document.querySelector("#evidence-file"),
  inputBadge: document.querySelector("#input-badge"),
  evidenceCard: document.querySelector("#evidence-upload-card"),
  evidenceBadge: document.querySelector("#evidence-required-badge"),
  savedProviderField: document.querySelector("#saved-provider-field"),
  provider: document.querySelector("#provider"),
  qualityVerified: document.querySelector("#quality-verified"),
  qualityVerifiedCard: document.querySelector("#verified-quality-card"),
  qualityVerifiedHelp: document.querySelector("#quality-verified-help"),
  bundleStatus: document.querySelector("#bundle-status"),
  bundleStatusTitle: document.querySelector("#bundle-status-title"),
  bundleStatusDetail: document.querySelector("#bundle-status-detail"),
  capabilityState: document.querySelector("#capability-state"),
  capabilityMessage: document.querySelector("#capability-message"),
  evidenceModePicker: document.querySelector("#evidence-mode-picker"),
  onlineEvidenceMode: document.querySelector("#online-evidence-mode"),
  onlineProviderField: document.querySelector("#online-provider-field"),
  onlineProvider: document.querySelector("#online-ocr-provider"),
  onlineProviderDetail: document.querySelector("#online-provider-detail"),
  onlineOcrOptions: document.querySelector("#online-ocr-options"),
  ocrLanguage: document.querySelector("#ocr-language"),
  ocrHandwriting: document.querySelector("#ocr-handwriting"),
  ocrFormulas: document.querySelector("#ocr-formulas"),
  ocrTables: document.querySelector("#ocr-tables"),
  ocrCharts: document.querySelector("#ocr-charts"),
  ocrDistorted: document.querySelector("#ocr-distorted-photo"),
  ocrDewarping: document.querySelector("#ocr-dewarping"),
  strictEvidence: document.querySelector("#strict-evidence"),
  remoteAssets: document.querySelector("#remote-assets"),
  remoteAssetsHelp: document.querySelector("#remote-assets-help"),
  paddleFallback: document.querySelector("#paddle-fallback"),
  paddleFallbackHelp: document.querySelector("#paddle-fallback-help"),
  outputName: document.querySelector("#output-name"),
  consent: document.querySelector("#upload-consent"),
  consentDetail: document.querySelector("#upload-consent-detail"),
  submit: document.querySelector("#submit-button"),
  submitHint: document.querySelector("#submit-hint"),
  error: document.querySelector("#form-error"),
  progressTitle: document.querySelector("#progress-title"),
  progressMessage: document.querySelector("#progress-message"),
  progressPercent: document.querySelector("#progress-percent"),
  progressTrack: document.querySelector("#progress-track"),
  progressBar: document.querySelector("#progress-bar"),
  resultArea: document.querySelector("#result-area"),
  resultFilename: document.querySelector("#result-filename"),
  resultMeta: document.querySelector("#result-meta"),
  downloadLink: document.querySelector("#download-link"),
};

let currentLanguage = chooseInitialLanguage();
let activeDownloadUrl = null;
let activeRequest = null;
let capabilityTimer = null;
let capabilityRequest = null;
let serviceCapabilities = null;

class FormInputError extends Error {
  constructor(key, values = {}, target = null) {
    super(key);
    this.values = values;
    this.target = target;
  }
}

function chooseInitialLanguage() {
  let storedLanguage = null;
  try {
    storedLanguage = window.localStorage.getItem("docreconstruct-language");
  } catch (_error) {
    // Language preference is optional; the client works when storage is blocked.
  }
  if (storedLanguage && supportedLanguages.includes(storedLanguage)) {
    return storedLanguage;
  }
  const browserLanguages = navigator.languages || [navigator.language];
  for (const language of browserLanguages) {
    const normalized = language.toLowerCase();
    if (normalized.startsWith("vi")) return "vi";
    if (normalized.startsWith("zh")) return "zh-CN";
    if (normalized.startsWith("ru")) return "ru";
    if (normalized.startsWith("en")) return "en";
  }
  return "vi";
}

function t(key, values = {}) {
  const fallback = translations.en[key] || key;
  const template = translations[currentLanguage][key] || fallback;
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  );
}

function applyLanguage(language) {
  currentLanguage = supportedLanguages.includes(language) ? language : "en";
  document.documentElement.lang = currentLanguage;
  document.title = t("pageTitle");
  elements.language.value = currentLanguage;

  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    node.setAttribute("aria-label", t(node.dataset.i18nAria));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.setAttribute("placeholder", t(node.dataset.i18nPlaceholder));
  });

  updateAllFileLabels();
  updateCapabilityUI();
  updateEvidenceModeUI();
  updateBundleStatus();
  updateConsentCopy();
  updateSubmitAvailability();
  try {
    window.localStorage.setItem("docreconstruct-language", currentLanguage);
  } catch (_error) {
    // Do not require persistent browser storage.
  }
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${new Intl.NumberFormat(currentLanguage, { maximumFractionDigits: exponent ? 1 : 0 }).format(value)} ${units[exponent]}`;
}

function updateFileLabel(input, targetId, emptyKey) {
  const target = document.querySelector(`#${targetId}`);
  const card = input.closest(".upload-card");
  const file = input.files && input.files[0];
  if (file) {
    target.textContent = t("selectedFile", { name: file.name, size: formatBytes(file.size) });
    card.classList.add("has-file");
  } else {
    target.textContent = t(emptyKey);
    card.classList.remove("has-file");
  }
}

function updateAllFileLabels() {
  updateFileLabel(elements.content, "content-file-name", "chooseFile");
  updateFileLabel(elements.layout, "layout-file-name", "chooseOriginal");
  updateFileLabel(elements.evidence, "evidence-file-name", "chooseJson");
}

function selectedEvidenceMode() {
  return document.querySelector('input[name="evidence-mode"]:checked')?.value || "upload";
}

function usesOnlineEvidence() {
  return selectedEvidenceMode() === "online";
}

function availableOnlineProviders() {
  if (!serviceCapabilities || serviceCapabilities.status !== "available") return [];
  return serviceCapabilities.providers;
}

function maximumUploadBytes() {
  if (serviceCapabilities?.status !== "available") return null;
  const megabytes = Number(serviceCapabilities.maximumUploadMb);
  return Number.isFinite(megabytes) && megabytes > 0 ? megabytes * 1024 * 1024 : null;
}

function oversizedUpload() {
  const maximum = maximumUploadBytes();
  if (!maximum) return null;
  const uploads = [
    { file: elements.layout.files?.[0], target: elements.layout },
    { file: elements.content.files?.[0], target: elements.content },
  ];
  if (!usesOnlineEvidence()) {
    uploads.push({ file: elements.evidence.files?.[0], target: elements.evidence });
  }
  const match = uploads.find(({ file }) => file && file.size > maximum);
  if (!match) return null;
  return {
    ...match,
    maximumLabel: `${serviceCapabilities.maximumUploadMb} MB`,
  };
}

function providerMetadataKey(value, category) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replaceAll("-", "_")
    .replaceAll(" ", "_");
  const known = {
    cost: {
      free: "providerCostFree",
      free_quota: "providerCostQuota",
      quota: "providerCostQuota",
      paid: "providerCostPaid",
      metered: "providerCostPaid",
      commercial: "providerCostPaid",
      infrastructure: "providerCostOperator",
      operator: "providerCostOperator",
      operator_managed: "providerCostOperator",
    },
    privacy: {
      local: "providerPrivacyLocal",
      operator: "providerPrivacyOperator",
      operator_policy: "providerPrivacyOperator",
      external: "providerPrivacyExternal",
      uploaded_to_provider: "providerPrivacyExternal",
      cloud: "providerPrivacyExternal",
      third_party: "providerPrivacyExternal",
      user_managed: "providerPrivacyOperator",
      no_transfer: "providerPrivacyLocal",
    },
  };
  return known[category][normalized] || null;
}

function localizedProviderMetadata(provider) {
  const privacyKey = providerMetadataKey(provider?.privacy, "privacy");
  const costKey = providerMetadataKey(provider?.cost, "cost");
  return t("providerDetail", {
    privacy: t(privacyKey || "providerPrivacyUnknown"),
    cost: t(costKey || "providerCostUnknown"),
  });
}

function currentOnlineProvider() {
  const name = elements.onlineProvider.value;
  return availableOnlineProviders().find((provider) => provider.name === name) || null;
}

function normalizedOriginalExtension(file) {
  const extension = file?.name?.split(".").pop()?.toLowerCase() || "";
  if (extension === "jpg") return "jpeg";
  if (extension === "tif") return "tiff";
  return extension;
}

function providerOriginalInputs(provider) {
  const allowed = new Set(["pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp", "gif", "bmp"]);
  return (provider?.supportedInputs || []).filter((value) => allowed.has(value));
}

function providerAcceptsOriginal(provider, file) {
  if (!provider || !file) return true;
  const supported = providerOriginalInputs(provider);
  if (!supported.length) return true;
  const normalized = normalizedOriginalExtension(file);
  if (normalized === "jpeg") return supported.includes("jpeg") || supported.includes("jpg");
  if (normalized === "tiff") return supported.includes("tiff") || supported.includes("tif");
  return supported.includes(normalized);
}

function displayOriginalInputs(provider) {
  const supported = new Set(providerOriginalInputs(provider));
  const labels = [];
  if (supported.has("pdf")) labels.push("PDF");
  if (supported.has("png")) labels.push("PNG");
  if (supported.has("jpeg") || supported.has("jpg")) labels.push("JPG/JPEG");
  if (supported.has("tiff") || supported.has("tif")) labels.push("TIF/TIFF");
  if (supported.has("webp")) labels.push("WebP");
  if (supported.has("gif")) labels.push("GIF");
  if (supported.has("bmp")) labels.push("BMP");
  return labels;
}

function updateProviderDetail() {
  const provider = currentOnlineProvider();
  const detailParts = provider ? [localizedProviderMetadata(provider)] : [];
  const inputLabels = displayOriginalInputs(provider);
  if (inputLabels.length) {
    detailParts.push(t("providerInputs", { types: inputLabels.join(", ") }));
  }
  elements.onlineProviderDetail.textContent = detailParts.join(" · ");

  const declaredCapabilities = provider?.capabilities;
  const capabilityNames = Array.isArray(declaredCapabilities)
    ? new Set(declaredCapabilities.map((value) => String(value)))
    : null;
  const capabilityInputs = {
    handwriting: elements.ocrHandwriting,
    formulas: elements.ocrFormulas,
    tables: elements.ocrTables,
    charts: elements.ocrCharts,
    distorted_photos: elements.ocrDistorted,
    dewarping: elements.ocrDewarping,
  };
  Object.entries(capabilityInputs).forEach(([name, input]) => {
    let supported = true;
    if (capabilityNames) supported = capabilityNames.has(name);
    else if (declaredCapabilities && typeof declaredCapabilities === "object") {
      supported = declaredCapabilities[name] !== false;
    }
    input.disabled = !supported;
    if (!supported) input.checked = false;
    input.closest(".check-row")?.classList.toggle("unavailable", !supported);
  });
}

function providerDisplayLabel(provider) {
  const localizedKeys = {
    paddleocr_official: "providerPaddleOfficial",
    paddleocr_vl_server: "providerPaddleCompatible",
    mistral_ocr: "providerMistral",
    google_document_ai: "providerGoogle",
    azure_document_intelligence: "providerAzure",
    mathpix: "providerMathpix",
  };
  const key = localizedKeys[provider.name];
  return key ? t(key) : provider.label || provider.name;
}

function renderOnlineProviders() {
  const previousValue = elements.onlineProvider.value;
  elements.onlineProvider.replaceChildren();
  availableOnlineProviders().forEach((provider) => {
    const option = document.createElement("option");
    option.value = provider.name;
    option.textContent = providerDisplayLabel(provider);
    elements.onlineProvider.append(option);
  });
  const stillAvailable = availableOnlineProviders().some(
    (provider) => provider.name === previousValue,
  );
  if (stillAvailable) elements.onlineProvider.value = previousValue;
  updateProviderDetail();
}

function updateEvidenceModeUI() {
  const providers = availableOnlineProviders();
  const onlineAvailable = providers.length > 0;
  elements.evidenceModePicker.hidden = !onlineAvailable;
  elements.onlineEvidenceMode.disabled = !onlineAvailable;

  if (!onlineAvailable && usesOnlineEvidence()) {
    const uploadChoice = document.querySelector('input[name="evidence-mode"][value="upload"]');
    uploadChoice.checked = true;
  }

  const online = onlineAvailable && usesOnlineEvidence();
  elements.onlineProviderField.hidden = !online;
  elements.onlineOcrOptions.hidden = !online;
  elements.savedProviderField.hidden = online;
  elements.provider.disabled = online;
  elements.evidence.required = !online;
  elements.evidence.disabled = online;
  elements.evidenceCard.classList.toggle("delegated", online);
  elements.evidenceBadge.className = online ? "optional" : "required";
  elements.evidenceBadge.textContent = t(online ? "evidenceGeneratedBadge" : "required");
  const paddleFallbackAvailable = providers.some(
    (provider) => provider.name === "paddleocr_vl_server",
  );
  const paddleFallbackDisabled = online || !paddleFallbackAvailable;
  elements.paddleFallback.disabled = paddleFallbackDisabled;
  if (paddleFallbackDisabled) elements.paddleFallback.checked = false;
  elements.paddleFallback
    .closest(".check-row")
    ?.classList.toggle("unavailable", paddleFallbackDisabled);
  elements.paddleFallbackHelp.textContent = t(
    paddleFallbackAvailable ? "paddleFallbackHelp" : "paddleFallbackUnavailable",
  );
  renderOnlineProviders();
  updateBundleStatus();
  updateConsentCopy();
  updateSubmitAvailability();
}

function updateBundleStatus() {
  const online = usesOnlineEvidence() && availableOnlineProviders().length > 0;
  const hasOriginal = Boolean(elements.layout.files?.[0]);
  const hasMarkdown = Boolean(elements.content.files?.[0]);
  const hasEvidence = online || Boolean(elements.evidence.files?.[0]);
  const originalSupported =
    !online || providerAcceptsOriginal(currentOnlineProvider(), elements.layout.files?.[0]);
  const oversized = oversizedUpload();
  const ready = hasOriginal && hasMarkdown && hasEvidence && originalSupported && !oversized;
  elements.inputBadge.textContent = t(online ? "onlineInputBadge" : "noBackendBadge");
  elements.inputBadge.classList.toggle("online", online);
  elements.bundleStatus.classList.toggle("ready", ready);
  elements.bundleStatus.classList.toggle(
    "invalid",
    Boolean(oversized) || (hasOriginal && !originalSupported),
  );
  elements.bundleStatusTitle.textContent = t(ready ? "bundleReadyTitle" : "bundleWaitingTitle");
  if (oversized) {
    elements.bundleStatusDetail.textContent = t("bundleTooLargeBody", {
      name: oversized.file.name,
      size: oversized.maximumLabel,
    });
  } else if (hasOriginal && !originalSupported) {
    elements.bundleStatusDetail.textContent = t("bundleUnsupportedBody");
  } else if (ready) {
    elements.bundleStatusDetail.textContent = t(
      online ? "bundleOnlineReadyBody" : "bundleReadyBody",
    );
  } else {
    elements.bundleStatusDetail.textContent = t(
      online ? "bundleOnlineWaitingBody" : "bundleWaitingBody",
    );
  }
}

function updateCapabilityUI() {
  const state = serviceCapabilities?.status || "waiting";
  const stateKey = {
    waiting: "capabilityWaiting",
    checking: "capabilityChecking",
    available: "capabilityAvailable",
    unavailable: "capabilityUnavailable",
  }[state];
  elements.capabilityState.textContent = t(stateKey);
  elements.capabilityState.className = `capability-state ${state}`;

  if (state === "checking") {
    elements.capabilityMessage.textContent = t("capabilityCheckingMessage");
  } else if (state === "available") {
    const hasOnline = availableOnlineProviders().length > 0;
    const parts = [t(hasOnline ? "capabilityOnlineAvailable" : "capabilityUploadOnly")];
    const maximumUploadMb = Number(serviceCapabilities.maximumUploadMb);
    if (Number.isFinite(maximumUploadMb) && maximumUploadMb > 0) {
      parts.push(t("capabilityLimit", { size: `${maximumUploadMb} MB` }));
    }
    elements.capabilityMessage.textContent = parts.join(" ");
  } else if (state === "unavailable") {
    elements.capabilityMessage.textContent = t("capabilityError");
  } else {
    elements.capabilityMessage.textContent = t("capabilityPrompt");
  }
  updateServiceFeatureAvailability();
}

function updateServiceFeatureAvailability() {
  const connected = serviceCapabilities?.status === "available";
  const verifiedAvailable = connected && serviceCapabilities.verifiedAvailable === true;
  const remoteAssetsAvailable = connected && serviceCapabilities.remoteAssetsAvailable === true;

  elements.qualityVerified.disabled = !verifiedAvailable;
  elements.qualityVerifiedCard.classList.toggle("unavailable", !verifiedAvailable);
  elements.qualityVerifiedHelp.textContent = t(
    verifiedAvailable ? "qualityVerifiedHelp" : "qualityVerifiedUnavailable",
  );
  if (!verifiedAvailable && elements.qualityVerified.checked) {
    document.querySelector('input[name="quality"][value="fast"]').checked = true;
  }

  elements.remoteAssets.disabled = !remoteAssetsAvailable;
  elements.remoteAssets.closest(".check-row")?.classList.toggle("unavailable", !remoteAssetsAvailable);
  elements.remoteAssetsHelp.textContent = t(
    remoteAssetsAvailable ? "remoteAssetsHelp" : "remoteAssetsUnavailable",
  );
  if (!remoteAssetsAvailable) elements.remoteAssets.checked = false;
}

function updateConsentCopy() {
  const backend = elements.backend.value.trim() || t("consentBackendUnset");
  elements.consentDetail.textContent = t("consentBody", { backend });
}

function updateSubmitAvailability() {
  const online = usesOnlineEvidence() && availableOnlineProviders().length > 0;
  const hasEvidence = online
    ? Boolean(elements.onlineProvider.value)
    : Boolean(elements.evidence.files?.[0]);
  const originalSupported =
    !online || providerAcceptsOriginal(currentOnlineProvider(), elements.layout.files?.[0]);
  const withinUploadLimit = !oversizedUpload();
  const hasRequiredInputs = Boolean(
    elements.backend.value.trim() &&
      elements.content.files?.[0] &&
      elements.layout.files?.[0] &&
      hasEvidence &&
      originalSupported &&
      withinUploadLimit,
  );
  const ready = hasRequiredInputs && elements.consent.checked;
  elements.submit.disabled = Boolean(activeRequest) || !ready;
  elements.submitHint.textContent = t(ready ? "submitReady" : "submitHint");
  elements.submitHint.classList.toggle("ready", ready);
  if (activeRequest) elements.submit.dataset.busy = "true";
  else delete elements.submit.dataset.busy;
}

function invalidateConsent() {
  elements.consent.checked = false;
  elements.consent.removeAttribute("aria-invalid");
  updateBundleStatus();
  updateConsentCopy();
  updateSubmitAvailability();
}

function normalizeEndpoint(rawValue) {
  const value = rawValue.trim();
  if (!value) throw new Error("errorBackendMissing");

  let endpoint;
  try {
    endpoint = new URL(value);
  } catch (_error) {
    throw new Error("errorBackendInvalid");
  }
  if (!['http:', 'https:'].includes(endpoint.protocol)) {
    throw new Error("errorBackendInvalid");
  }
  if (endpoint.username || endpoint.password) {
    throw new Error("errorBackendCredentials");
  }

  const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  if (
    window.location.protocol === "https:" &&
    endpoint.protocol === "http:" &&
    !loopbackHosts.has(endpoint.hostname)
  ) {
    throw new Error("errorInsecureBackend");
  }

  endpoint.search = "";
  endpoint.hash = "";
  const cleanPath = endpoint.pathname.replace(/\/+$/, "");
  endpoint.pathname = cleanPath.endsWith("/v1/hybrid")
    ? cleanPath
    : `${cleanPath}/v1/hybrid`.replace(/\/{2,}/g, "/");
  return endpoint.toString();
}

function capabilityEndpoint(rawValue) {
  const endpoint = new URL(normalizeEndpoint(rawValue));
  endpoint.pathname = `${endpoint.pathname.replace(/\/+$/, "")}/capabilities`;
  return endpoint.toString();
}

function providersFromCapabilities(payload) {
  if (!payload || typeof payload !== "object") return [];
  const candidates =
    payload.hosted_ocr_providers || payload.ocr_providers || payload.providers || [];
  if (!Array.isArray(candidates)) return [];
  return candidates
    .filter(
      (provider) =>
        provider &&
        typeof provider === "object" &&
        typeof provider.name === "string" &&
        provider.name.trim() &&
        provider.available !== false,
    )
    .map((provider) => ({
      name: provider.name.trim(),
      label:
        typeof provider.label === "string" && provider.label.trim()
          ? provider.label.trim()
          : provider.name.trim(),
      cost: provider.cost,
      privacy: provider.privacy,
      capabilities: provider.capabilities,
      supportedInputs: Array.isArray(provider.supported_inputs)
        ? provider.supported_inputs.map((value) => String(value).toLowerCase())
        : [],
    }));
}

async function probeCapabilities() {
  const rawValue = elements.backend.value.trim();
  if (capabilityRequest) capabilityRequest.abort();
  capabilityRequest = null;

  if (!rawValue) {
    serviceCapabilities = null;
    updateCapabilityUI();
    updateEvidenceModeUI();
    return;
  }

  let endpoint;
  try {
    endpoint = capabilityEndpoint(rawValue);
  } catch (_error) {
    serviceCapabilities = { status: "unavailable", providers: [] };
    updateCapabilityUI();
    updateEvidenceModeUI();
    return;
  }

  const controller = new AbortController();
  capabilityRequest = controller;
  serviceCapabilities = { status: "checking", providers: [] };
  updateCapabilityUI();
  updateEvidenceModeUI();

  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(endpoint, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
      credentials: "omit",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || typeof payload !== "object") throw new Error("Invalid capabilities");
    const evidenceModes = Array.isArray(payload.evidence_modes) ? payload.evidence_modes : [];
    const hostedOcrAvailable =
      payload.server_generates_json === true && evidenceModes.includes("hosted_ocr");
    serviceCapabilities = {
      status: "available",
      providers: hostedOcrAvailable ? providersFromCapabilities(payload) : [],
      maximumUploadMb: payload.maximum_upload_mb,
      verifiedAvailable: payload.verified_available === true,
      remoteAssetsAvailable: payload.remote_assets_available === true,
    };
  } catch (_error) {
    if (capabilityRequest !== controller) return;
    serviceCapabilities = { status: "unavailable", providers: [] };
  } finally {
    window.clearTimeout(timeout);
    if (capabilityRequest === controller) capabilityRequest = null;
  }
  updateCapabilityUI();
  updateEvidenceModeUI();
}

function scheduleCapabilityProbe() {
  if (capabilityTimer) window.clearTimeout(capabilityTimer);
  if (capabilityRequest) capabilityRequest.abort();
  capabilityRequest = null;
  serviceCapabilities = elements.backend.value.trim()
    ? { status: "checking", providers: [] }
    : null;
  updateCapabilityUI();
  updateEvidenceModeUI();
  capabilityTimer = window.setTimeout(probeCapabilities, 500);
}

function selectedQuality() {
  return document.querySelector('input[name="quality"]:checked').value;
}

function validateInputs() {
  const endpoint = normalizeEndpoint(elements.backend.value);
  const content = elements.content.files[0];
  const layout = elements.layout.files[0];
  const onlineEvidence = usesOnlineEvidence();
  const onlineProvider = elements.onlineProvider.value;
  const selectedEvidence = elements.evidence.files[0];
  const evidence = onlineEvidence ? null : selectedEvidence;
  if (!content) throw new Error("errorContentMissing");
  if (!layout) throw new Error("errorLayoutMissing");
  if (evidence && !evidence.name.toLowerCase().endsWith(".json")) {
    throw new Error("errorEvidenceType");
  }
  if (!onlineEvidence && !evidence) {
    throw new Error("errorEvidenceMissing");
  }
  if (onlineEvidence && !onlineProvider) {
    throw new Error("errorOnlineProviderMissing");
  }
  const oversized = oversizedUpload();
  if (oversized) {
    throw new FormInputError(
      "errorFileTooLarge",
      { name: oversized.file.name, size: oversized.maximumLabel },
      oversized.target,
    );
  }
  if (onlineEvidence && !providerAcceptsOriginal(currentOnlineProvider(), layout)) {
    throw new Error("errorUnsupportedOriginal");
  }
  if (!onlineEvidence && elements.provider.value && !evidence) {
    throw new Error("errorProviderWithoutJson");
  }
  const outputName = elements.outputName.value.trim();
  if (outputName.includes("/") || outputName.includes("\\")) {
    throw new Error("errorOutputName");
  }
  if (!elements.consent.checked) {
    throw new Error("errorConsentRequired");
  }
  return { endpoint, content, layout, evidence, onlineEvidence, onlineProvider, outputName };
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.hidden = false;
  elements.error.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function clearError() {
  elements.error.hidden = true;
  elements.error.textContent = "";
  [
    elements.backend,
    elements.content,
    elements.layout,
    elements.evidence,
    elements.provider,
    elements.onlineProvider,
    elements.outputName,
    elements.consent,
  ].forEach((element) => element.removeAttribute("aria-invalid"));
}

function focusInvalidField(errorKey, overrideTarget = null) {
  const targetByError = {
    errorBackendMissing: elements.backend,
    errorBackendInvalid: elements.backend,
    errorBackendCredentials: elements.backend,
    errorInsecureBackend: elements.backend,
    errorContentMissing: elements.content,
    errorLayoutMissing: elements.layout,
    errorEvidenceType: elements.evidence,
    errorEvidenceMissing: elements.evidence,
    errorOnlineProviderMissing: elements.onlineProvider,
    errorUnsupportedOriginal: elements.layout,
    errorProviderWithoutJson: elements.provider,
    errorOutputName: elements.outputName,
    errorConsentRequired: elements.consent,
  };
  const target = overrideTarget || targetByError[errorKey];
  if (!target) return;
  target.setAttribute("aria-invalid", "true");
  target.focus();
}

function setProgress(percent, titleKey, messageKey, processing = false) {
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
  elements.progressTitle.textContent = t(titleKey);
  elements.progressMessage.textContent = t(messageKey);
  elements.progressPercent.textContent = processing ? "…" : `${safePercent}%`;
  elements.progressTrack.classList.toggle("processing", processing);
  if (processing) {
    elements.progressTrack.removeAttribute("aria-valuenow");
    elements.progressTrack.setAttribute("aria-valuetext", t(messageKey));
  } else {
    elements.progressTrack.setAttribute("aria-valuenow", String(safePercent));
    elements.progressTrack.removeAttribute("aria-valuetext");
    elements.progressBar.style.width = `${safePercent}%`;
  }
}

function extractFilename(headerValue, fallback) {
  if (!headerValue) return fallback;
  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = headerValue.match(/filename="?([^";]+)"?/i);
  let filename = fallback;
  try {
    if (utf8Match) filename = decodeURIComponent(utf8Match[1]);
    else if (plainMatch) filename = plainMatch[1];
  } catch (_error) {
    filename = fallback;
  }
  filename = filename.split(/[\\/]/).pop().trim();
  return filename.toLowerCase().endsWith(".docx") ? filename : `${filename}.docx`;
}

function fallbackFilename(contentFile, requestedName) {
  if (requestedName) {
    return requestedName.toLowerCase().endsWith(".docx") ? requestedName : `${requestedName}.docx`;
  }
  const stem = contentFile.name.replace(/\.[^.]+$/, "") || "document";
  return `${stem}_editable.docx`;
}

function detailFromPayload(payload, status) {
  if (payload && typeof payload === "object") {
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      const messages = payload.detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : null))
        .filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
    if (typeof payload.message === "string") return payload.message;
  }
  return t("serverError", { status });
}

async function responseError(blob, status) {
  try {
    const body = await blob.text();
    if (!body) return t("serverError", { status });
    try {
      return detailFromPayload(JSON.parse(body), status);
    } catch (_error) {
      return body.slice(0, 500);
    }
  } catch (_error) {
    return t("serverError", { status });
  }
}

function resetResult() {
  elements.resultArea.hidden = true;
  if (activeDownloadUrl) {
    URL.revokeObjectURL(activeDownloadUrl);
    activeDownloadUrl = null;
  }
}

function completeDownload(blob, filename, quality, xhr) {
  activeDownloadUrl = URL.createObjectURL(blob);
  elements.downloadLink.href = activeDownloadUrl;
  elements.downloadLink.download = filename;
  elements.resultFilename.textContent = filename;

  const qualityName = t(quality === "verified" ? "qualityVerifiedResult" : "qualityFastResult");
  const metaParts = [t("resultMeta", { size: formatBytes(blob.size), quality: qualityName })];
  // The native QA score is a pass ratio for structural gates, not a pixel
  // similarity measurement. Only show visual similarity when the verified
  // backend returns the dedicated rendered-visual header.
  const visualScore = xhr.getResponseHeader("X-DocReconstruct-Visual-Score");
  if (visualScore && Number.isFinite(Number(visualScore))) {
    const numericScore = Number(visualScore);
    const percentage = numericScore <= 1 ? numericScore * 100 : numericScore;
    metaParts.push(t("visualScore", { score: `${Math.round(percentage)}%` }));
  }
  const duration = Number(xhr.getResponseHeader("X-DocReconstruct-Duration"));
  if (Number.isFinite(duration) && duration >= 0) {
    const seconds = new Intl.NumberFormat(currentLanguage, {
      maximumFractionDigits: duration < 10 ? 1 : 0,
    }).format(duration);
    metaParts.push(t("serverDuration", { seconds }));
  }
  elements.resultMeta.textContent = metaParts.join(" · ");
  elements.resultArea.hidden = false;

  const automaticDownload = document.createElement("a");
  automaticDownload.href = activeDownloadUrl;
  automaticDownload.download = filename;
  automaticDownload.hidden = true;
  document.body.append(automaticDownload);
  automaticDownload.click();
  automaticDownload.remove();
}

async function handleSuccess(xhr, requestData, quality) {
  const blob = xhr.response;
  if (!(blob instanceof Blob) || blob.size === 0) {
    throw new Error("unknownError");
  }
  const fallback = fallbackFilename(requestData.content, requestData.outputName);
  const filename = extractFilename(xhr.getResponseHeader("Content-Disposition"), fallback);
  setProgress(100, "finishedTitle", "finishedMessage");
  completeDownload(blob, filename, quality, xhr);
}

function submitJob(event) {
  event.preventDefault();
  if (activeRequest) return;

  clearError();
  resetResult();
  let requestData;
  try {
    requestData = validateInputs();
  } catch (error) {
    const key = error instanceof Error ? error.message : "unknownError";
    const values = error instanceof FormInputError ? error.values : {};
    showError(t(key, values));
    focusInvalidField(key, error instanceof FormInputError ? error.target : null);
    return;
  }

  const quality = selectedQuality();
  const options = {
    quality,
    strict_evidence: elements.strictEvidence.checked,
    remote_assets: elements.remoteAssets.checked,
    use_paddleocr_vl: requestData.onlineEvidence ? false : elements.paddleFallback.checked,
  };
  if (requestData.onlineEvidence) {
    options.ocr_provider = requestData.onlineProvider;
    const language = elements.ocrLanguage.value;
    if (language) options.ocr_languages = [language];
    options.ocr_handwriting = elements.ocrHandwriting.checked;
    options.ocr_formulas = elements.ocrFormulas.checked;
    options.ocr_tables = elements.ocrTables.checked;
    options.ocr_charts = elements.ocrCharts.checked;
    options.ocr_distorted_photo = elements.ocrDistorted.checked;
    options.ocr_dewarping = elements.ocrDewarping.checked;
  } else if (elements.provider.value) {
    options.evidence_provider = elements.provider.value;
  }
  if (requestData.outputName) options.output_filename = requestData.outputName;

  const body = new FormData();
  body.append("content", requestData.content, requestData.content.name);
  body.append("layout", requestData.layout, requestData.layout.name);
  if (requestData.evidence) body.append("evidence", requestData.evidence, requestData.evidence.name);
  body.append("options", JSON.stringify(options));

  setProgress(3, "preparingTitle", "preparingMessage");

  const xhr = new XMLHttpRequest();
  activeRequest = xhr;
  updateSubmitAvailability();
  xhr.open("POST", requestData.endpoint, true);
  xhr.responseType = "blob";
  xhr.timeout = 10 * 60 * 1000;

  xhr.upload.addEventListener("loadstart", () => {
    setProgress(7, "uploadingTitle", "uploadingMessage");
  });
  xhr.upload.addEventListener("progress", (progressEvent) => {
    if (!progressEvent.lengthComputable) return;
    const uploaded = progressEvent.loaded / progressEvent.total;
    setProgress(7 + uploaded * 76, "uploadingTitle", "uploadingMessage");
  });
  xhr.upload.addEventListener("load", () => {
    setProgress(
      86,
      "processingTitle",
      quality === "verified" ? "processingVerified" : "processingFast",
      true,
    );
  });

  xhr.addEventListener("load", async () => {
    try {
      if (xhr.status >= 200 && xhr.status < 300) {
        await handleSuccess(xhr, requestData, quality);
      } else {
        const message = await responseError(xhr.response, xhr.status);
        throw new Error(message);
      }
    } catch (error) {
      const rawMessage = error instanceof Error ? error.message : "unknownError";
      const message = translations[currentLanguage][rawMessage] ? t(rawMessage) : rawMessage;
      setProgress(0, "failedTitle", "unknownError");
      elements.progressMessage.textContent = message;
      showError(message);
    } finally {
      activeRequest = null;
      updateSubmitAvailability();
    }
  });

  xhr.addEventListener("error", () => {
    const message = t("networkError");
    setProgress(0, "failedTitle", "networkError");
    showError(message);
    activeRequest = null;
    updateSubmitAvailability();
  });

  xhr.addEventListener("timeout", () => {
    const message = t("timeoutError");
    setProgress(0, "failedTitle", "timeoutError");
    showError(message);
    activeRequest = null;
    updateSubmitAvailability();
  });

  xhr.send(body);
}

elements.language.addEventListener("change", (event) => applyLanguage(event.target.value));
function handleSourceFileChange() {
  updateAllFileLabels();
  invalidateConsent();
}

elements.content.addEventListener("change", handleSourceFileChange);
elements.layout.addEventListener("change", handleSourceFileChange);
elements.evidence.addEventListener("change", handleSourceFileChange);
elements.backend.addEventListener("input", () => {
  invalidateConsent();
  scheduleCapabilityProbe();
});
document.querySelectorAll('input[name="evidence-mode"]').forEach((input) => {
  input.addEventListener("change", () => {
    updateEvidenceModeUI();
    invalidateConsent();
  });
});
elements.onlineProvider.addEventListener("change", () => {
  updateProviderDetail();
  invalidateConsent();
});
elements.paddleFallback.addEventListener("change", invalidateConsent);
elements.remoteAssets.addEventListener("change", invalidateConsent);
elements.consent.addEventListener("change", () => {
  if (elements.consent.checked) elements.consent.removeAttribute("aria-invalid");
  updateSubmitAvailability();
});
elements.form.addEventListener("submit", submitJob);
window.addEventListener("beforeunload", () => {
  if (activeDownloadUrl) URL.revokeObjectURL(activeDownloadUrl);
  if (capabilityRequest) capabilityRequest.abort();
  if (capabilityTimer) window.clearTimeout(capabilityTimer);
});

applyLanguage(currentLanguage);
