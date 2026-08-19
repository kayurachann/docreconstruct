"use strict";

const translations = {
  vi: {
    pageTitle: "docreconstruct — Dựng lại tài liệu có thể chỉnh sửa",
    skipLink: "Đi tới biểu mẫu",
    languageLabel: "Ngôn ngữ",
    sourceCodeAria: "Mở mã nguồn trên GitHub",
    sourceCode: "Mã nguồn",
    eyebrow: "Giao diện web nguồn mở",
    title: "Dựng lại tài liệu có thể chỉnh sửa",
    subtitle:
      "Kết hợp nội dung Markdown, tệp gốc và dữ liệu bố cục tùy chọn để tạo tệp Word giữ nguyên cấu trúc tốt nhất có thể.",
    factsAria: "Thông tin chính",
    factOne: "Không tải tệp lên GitHub",
    factTwo: "Đầu ra DOCX có thể chỉnh sửa",
    factThree: "Bạn tự chọn máy chủ xử lý",
    formStep: "Bước 1–3",
    formTitle: "Chuẩn bị tác vụ",
    noBackendBadge: "Chưa kèm máy chủ",
    serverSection: "Chọn máy chủ xử lý",
    backendLabel: "Địa chỉ máy chủ",
    required: "Bắt buộc",
    backendPlaceholder: "https://may-chu-cua-ban.example",
    backendHelp: "Trang này sẽ gọi đường dẫn /v1/hybrid trên máy chủ bạn nhập.",
    noPublicBackendTitle: "Không kèm máy chủ công cộng.",
    noPublicBackendBody:
      "Trang này không thể xử lý tài liệu nếu bạn chưa cấu hình một máy chủ tương thích.",
    filesSection: "Thêm tệp nguồn",
    markdownLabel: "Nội dung Markdown",
    markdownHelp: "Văn bản, công thức và liên kết ảnh",
    chooseFile: "Chọn tệp .md",
    originalLabel: "Bản gốc PDF hoặc ảnh",
    originalHelp: "Nguồn tham chiếu cho bố cục và vị trí",
    chooseOriginal: "Chọn tệp gốc",
    jsonLabel: "Dữ liệu bố cục JSON",
    jsonHelp: "Tọa độ và thứ tự đọc từ công cụ OCR",
    chooseJson: "Chọn tệp .json",
    optional: "Không bắt buộc",
    optionsSection: "Chọn cách xử lý",
    providerLabel: "Nguồn dữ liệu bố cục",
    providerAuto: "Tự nhận diện",
    providerHelp: "Chỉ chọn khi bạn biết nguồn tạo tệp JSON.",
    qualityLabel: "Mức kiểm chứng",
    qualityFast: "Nhanh",
    qualityFastHelp: "Trả DOCX ngay sau khi dựng",
    qualityVerified: "Kiểm chứng",
    qualityVerifiedHelp: "Máy chủ dựng ảnh để đối chiếu",
    advancedTitle: "Tùy chọn nâng cao",
    strictLabel: "Kiểm tra JSON nghiêm ngặt",
    strictHelp: "Dừng lại nếu dữ liệu bố cục không hợp lệ.",
    remoteAssetsLabel: "Cho phép tải ảnh từ liên kết trong Markdown",
    remoteAssetsHelp:
      "Máy chủ có thể kết nối tới địa chỉ bên ngoài; chỉ hoạt động khi đơn vị vận hành đã cho phép.",
    paddleFallbackLabel: "Cho phép máy chủ dùng PaddleOCR-VL",
    paddleFallbackHelp: "Chỉ hoạt động khi quản trị viên đã cấu hình dịch vụ.",
    outputNameLabel: "Tên tệp đầu ra",
    outputNamePlaceholder: "tai_lieu_chinh_sua_duoc.docx",
    consentTitle: "Tôi đồng ý gửi tệp tới máy chủ đã chọn",
    consentBody:
      "Tệp sẽ được tải lên {backend}. Đơn vị vận hành máy chủ hoặc nhà cung cấp dịch vụ có thể xử lý và lưu giữ tệp theo chính sách của họ. Nếu bạn bật PaddleOCR-VL, máy chủ có thể chuyển tiếp tệp tới dịch vụ OCR do đơn vị vận hành cấu hình. Nếu cho phép ảnh liên kết, máy chủ có thể kết nối tới các địa chỉ trong Markdown.",
    consentBackendUnset: "máy chủ chưa được chọn",
    submit: "Dựng tệp Word",
    sideAria: "Thông tin tác vụ",
    resultStep: "Trạng thái",
    progressReadyTitle: "Sẵn sàng bắt đầu",
    progressReadyMessage: "Chọn máy chủ và các tệp nguồn để dựng tài liệu.",
    progressAria: "Tiến độ dựng tài liệu",
    downloadAgain: "Tải lại",
    privacyTitle: "Quyền riêng tư rõ ràng",
    privacyBody:
      "GitHub Pages chỉ phân phối giao diện tĩnh này. Tệp được gửi thẳng từ trình duyệt tới máy chủ bạn chọn; GitHub không xử lý tài liệu và trang này không lưu khóa truy cập.",
    costTitle: "Mã nguồn mở không đồng nghĩa GPU miễn phí",
    costBody:
      "Giao diện và dự án là mã nguồn mở. Máy chủ, điện năng, GPU hoặc dịch vụ OCR bên thứ ba vẫn có thể phát sinh chi phí theo cấu hình của đơn vị vận hành.",
    accuracyTitle: "Luôn kiểm tra kết quả",
    accuracyBody:
      "OCR và dựng bố cục có thể sai chính tả, công thức hoặc vị trí. Hãy đối chiếu DOCX với bản gốc trước khi sử dụng.",
    footerText: "Giao diện cộng đồng cho docreconstruct",
    noScript: "Bạn cần bật JavaScript để gửi tài liệu tới máy chủ đã chọn.",
    selectedFile: "Đã chọn: {name} ({size})",
    errorBackendMissing: "Hãy nhập địa chỉ máy chủ xử lý.",
    errorBackendInvalid: "Địa chỉ máy chủ không hợp lệ. Hãy nhập đầy đủ, gồm http:// hoặc https://.",
    errorBackendCredentials: "Không đặt tên đăng nhập hoặc mật khẩu trong địa chỉ máy chủ.",
    errorInsecureBackend:
      "Trang HTTPS không thể gọi máy chủ HTTP này. Hãy dùng HTTPS hoặc máy chủ cục bộ localhost.",
    errorContentMissing: "Hãy chọn tệp Markdown chứa nội dung.",
    errorLayoutMissing: "Hãy chọn tệp PDF hoặc ảnh gốc.",
    errorEvidenceType: "Tệp dữ liệu bố cục phải có phần mở rộng .json.",
    errorProviderWithoutJson: "Hãy thêm tệp JSON hoặc để nguồn dữ liệu ở chế độ tự nhận diện.",
    errorOutputName: "Tên tệp đầu ra không được chứa dấu gạch chéo.",
    errorConsentRequired: "Bạn phải đồng ý với việc gửi và xử lý tệp trước khi tiếp tục.",
    preparingTitle: "Đang chuẩn bị tệp",
    preparingMessage: "Đang kiểm tra đầu vào trước khi gửi.",
    uploadingTitle: "Đang gửi tới máy chủ",
    uploadingMessage: "Trình duyệt đang gửi trực tiếp các tệp nguồn.",
    processingTitle: "Máy chủ đang dựng tài liệu",
    processingFast: "Đang phân tích bố cục và tạo tệp DOCX có thể chỉnh sửa.",
    processingVerified: "Đang tạo DOCX và dựng ảnh để kiểm chứng trực quan.",
    finishedTitle: "Đã dựng xong",
    finishedMessage: "Tệp Word đã sẵn sàng và quá trình tải xuống đã bắt đầu.",
    failedTitle: "Không thể hoàn tất",
    networkError:
      "Không thể kết nối máy chủ. Hãy kiểm tra địa chỉ, trạng thái máy chủ và cấu hình CORS.",
    timeoutError: "Máy chủ không phản hồi trong thời gian cho phép.",
    serverError: "Máy chủ trả về lỗi {status}.",
    unknownError: "Đã xảy ra lỗi không xác định.",
    resultMeta: "{size} · chế độ {quality}",
    qualityFastResult: "nhanh",
    qualityVerifiedResult: "kiểm chứng",
    visualScore: "độ tương đồng {score}",
    serverDuration: "xử lý trong {seconds} giây",
  },
  en: {
    pageTitle: "docreconstruct — Rebuild editable documents",
    skipLink: "Skip to the form",
    languageLabel: "Language",
    sourceCodeAria: "Open the source code on GitHub",
    sourceCode: "Source code",
    eyebrow: "Open-source web client",
    title: "Rebuild editable documents",
    subtitle:
      "Combine Markdown content, the original file, and optional layout evidence to create an editable Word document that preserves the source structure as closely as possible.",
    factsAria: "Key information",
    factOne: "Files are not uploaded to GitHub",
    factTwo: "Editable DOCX output",
    factThree: "You choose the processing server",
    formStep: "Steps 1–3",
    formTitle: "Prepare the job",
    noBackendBadge: "No server included",
    serverSection: "Choose a processing server",
    backendLabel: "Server address",
    required: "Required",
    backendPlaceholder: "https://your-server.example",
    backendHelp: "This page calls /v1/hybrid on the server you enter.",
    noPublicBackendTitle: "No public processing server is bundled.",
    noPublicBackendBody:
      "This page cannot process a document until you configure a compatible server.",
    filesSection: "Add source files",
    markdownLabel: "Markdown content",
    markdownHelp: "Text, formulas, and image links",
    chooseFile: "Choose a .md file",
    originalLabel: "Original PDF or image",
    originalHelp: "Reference for layout and positioning",
    chooseOriginal: "Choose the original file",
    jsonLabel: "JSON layout evidence",
    jsonHelp: "Coordinates and reading order from an OCR tool",
    chooseJson: "Choose a .json file",
    optional: "Optional",
    optionsSection: "Choose how to process it",
    providerLabel: "Layout evidence source",
    providerAuto: "Detect automatically",
    providerHelp: "Select a source only if you know which tool created the JSON file.",
    qualityLabel: "Verification level",
    qualityFast: "Fast",
    qualityFastHelp: "Return the DOCX as soon as it is built",
    qualityVerified: "Verified",
    qualityVerifiedHelp: "Render the output for a visual check",
    advancedTitle: "Advanced options",
    strictLabel: "Validate JSON strictly",
    strictHelp: "Stop if the layout evidence is invalid.",
    remoteAssetsLabel: "Allow images linked from Markdown",
    remoteAssetsHelp:
      "The server may connect to external addresses; this works only when its operator allows it.",
    paddleFallbackLabel: "Allow the server to use PaddleOCR-VL",
    paddleFallbackHelp: "Works only when the server operator has configured the service.",
    outputNameLabel: "Output filename",
    outputNamePlaceholder: "editable_document.docx",
    consentTitle: "I agree to send the files to the selected server",
    consentBody:
      "The files will be uploaded to {backend}. The server operator or service provider may process and retain them under its own policy. If you enable PaddleOCR-VL, the server may forward the files to an OCR service configured by its operator. If linked images are allowed, the server may connect to addresses in the Markdown.",
    consentBackendUnset: "a server that has not yet been selected",
    submit: "Build Word document",
    sideAria: "Job information",
    resultStep: "Status",
    progressReadyTitle: "Ready to begin",
    progressReadyMessage: "Choose a server and the source files to build the document.",
    progressAria: "Document reconstruction progress",
    downloadAgain: "Download again",
    privacyTitle: "Clear privacy boundaries",
    privacyBody:
      "GitHub Pages serves only this static client. Files travel directly from your browser to the server you choose; GitHub does not process the document, and this page does not store access keys.",
    costTitle: "Open source does not mean free GPU compute",
    costBody:
      "The client and project are open source. Servers, electricity, GPUs, or third-party OCR services may still cost money, depending on the operator's setup.",
    accuracyTitle: "Always review the result",
    accuracyBody:
      "OCR and layout reconstruction can introduce spelling, formula, or positioning errors. Compare the DOCX with the original before relying on it.",
    footerText: "Community web client for docreconstruct",
    noScript: "Enable JavaScript to send documents to the server you choose.",
    selectedFile: "Selected: {name} ({size})",
    errorBackendMissing: "Enter the address of a processing server.",
    errorBackendInvalid: "The server address is invalid. Include http:// or https://.",
    errorBackendCredentials: "Do not put a username or password in the server address.",
    errorInsecureBackend:
      "This HTTPS page cannot call that HTTP server. Use HTTPS or a local localhost server.",
    errorContentMissing: "Choose the Markdown file that contains the content.",
    errorLayoutMissing: "Choose the original PDF or image.",
    errorEvidenceType: "Layout evidence must be a .json file.",
    errorProviderWithoutJson: "Add a JSON file or leave the evidence source on automatic detection.",
    errorOutputName: "The output filename cannot contain a slash.",
    errorConsentRequired: "You must agree to the file upload and processing terms before continuing.",
    preparingTitle: "Preparing files",
    preparingMessage: "Checking the inputs before upload.",
    uploadingTitle: "Uploading to the server",
    uploadingMessage: "Your browser is sending the source files directly.",
    processingTitle: "The server is rebuilding the document",
    processingFast: "Analysing the layout and creating an editable DOCX.",
    processingVerified: "Creating the DOCX and rendering it for visual verification.",
    finishedTitle: "Reconstruction complete",
    finishedMessage: "The Word document is ready, and the download has started.",
    failedTitle: "Could not complete the job",
    networkError: "Could not reach the server. Check its address, status, and CORS configuration.",
    timeoutError: "The server did not respond within the allowed time.",
    serverError: "The server returned error {status}.",
    unknownError: "An unknown error occurred.",
    resultMeta: "{size} · {quality} mode",
    qualityFastResult: "fast",
    qualityVerifiedResult: "verified",
    visualScore: "visual similarity {score}",
    serverDuration: "processed in {seconds} seconds",
  },
  "zh-CN": {
    pageTitle: "docreconstruct — 重建可编辑文档",
    skipLink: "跳转到表单",
    languageLabel: "语言",
    sourceCodeAria: "在 GitHub 上查看源代码",
    sourceCode: "源代码",
    eyebrow: "开源网页客户端",
    title: "重建可编辑文档",
    subtitle: "结合 Markdown 内容、原始文件和可选的版面数据，尽可能忠实地生成可编辑的 Word 文档。",
    factsAria: "重要说明",
    factOne: "文件不会上传到 GitHub",
    factTwo: "输出可编辑的 DOCX",
    factThree: "处理服务器由您选择",
    formStep: "第 1–3 步",
    formTitle: "准备任务",
    noBackendBadge: "不附带服务器",
    serverSection: "选择处理服务器",
    backendLabel: "服务器地址",
    required: "必填",
    backendPlaceholder: "https://您的服务器.example",
    backendHelp: "本页面会调用所填服务器上的 /v1/hybrid 路径。",
    noPublicBackendTitle: "本页面不附带公共处理服务器。",
    noPublicBackendBody: "只有在您配置了兼容的服务器后，本页面才能处理文档。",
    filesSection: "添加源文件",
    markdownLabel: "Markdown 内容",
    markdownHelp: "正文、公式和图片链接",
    chooseFile: "选择 .md 文件",
    originalLabel: "原始 PDF 或图片",
    originalHelp: "用于参照版面与元素位置",
    chooseOriginal: "选择原始文件",
    jsonLabel: "JSON 版面数据",
    jsonHelp: "OCR 工具给出的坐标与阅读顺序",
    chooseJson: "选择 .json 文件",
    optional: "选填",
    optionsSection: "选择处理方式",
    providerLabel: "版面数据来源",
    providerAuto: "自动识别",
    providerHelp: "仅在确定 JSON 文件由哪个工具生成时选择。",
    qualityLabel: "校验级别",
    qualityFast: "快速",
    qualityFastHelp: "文档生成后立即返回 DOCX",
    qualityVerified: "视觉校验",
    qualityVerifiedHelp: "渲染输出并进行视觉比对",
    advancedTitle: "高级选项",
    strictLabel: "严格校验 JSON",
    strictHelp: "版面数据无效时停止处理。",
    remoteAssetsLabel: "允许下载 Markdown 中链接的图片",
    remoteAssetsHelp: "服务器可能会连接外部地址；仅在运营方明确允许时生效。",
    paddleFallbackLabel: "允许服务器使用 PaddleOCR-VL",
    paddleFallbackHelp: "仅当服务器管理员已配置该服务时生效。",
    outputNameLabel: "输出文件名",
    outputNamePlaceholder: "可编辑文档.docx",
    consentTitle: "我同意将文件发送到所选服务器",
    consentBody:
      "文件将上传至{backend}。服务器运营方或服务提供商可能依据其政策处理和保留这些文件。若启用 PaddleOCR-VL，服务器可能把文件转发至运营方配置的 OCR 服务。若允许链接图片，服务器还可能访问 Markdown 中的外部地址。",
    consentBackendUnset: "尚未选择的服务器",
    submit: "生成 Word 文档",
    sideAria: "任务信息",
    resultStep: "状态",
    progressReadyTitle: "可以开始",
    progressReadyMessage: "请选择服务器和源文件以开始重建文档。",
    progressAria: "文档重建进度",
    downloadAgain: "再次下载",
    privacyTitle: "清晰的隐私边界",
    privacyBody:
      "GitHub Pages 只提供这个静态客户端。文件会从浏览器直接发往您选择的服务器；GitHub 不处理文档，本页面也不保存访问密钥。",
    costTitle: "开源不等于免费 GPU 算力",
    costBody:
      "客户端和项目均为开源软件，但服务器、电力、GPU 或第三方 OCR 服务仍可能产生费用，具体取决于运营方的配置。",
    accuracyTitle: "请务必复核结果",
    accuracyBody: "OCR 与版面重建可能出现文字、公式或位置错误。使用前请将 DOCX 与原件逐项核对。",
    footerText: "docreconstruct 社区网页客户端",
    noScript: "请启用 JavaScript，才能把文档发送到您选择的服务器。",
    selectedFile: "已选择：{name}（{size}）",
    errorBackendMissing: "请输入处理服务器的地址。",
    errorBackendInvalid: "服务器地址无效，请填写包含 http:// 或 https:// 的完整地址。",
    errorBackendCredentials: "请勿在服务器地址中填写用户名或密码。",
    errorInsecureBackend: "HTTPS 页面无法调用该 HTTP 服务器。请使用 HTTPS 或本机 localhost 服务。",
    errorContentMissing: "请选择包含正文的 Markdown 文件。",
    errorLayoutMissing: "请选择原始 PDF 或图片。",
    errorEvidenceType: "版面数据必须是 .json 文件。",
    errorProviderWithoutJson: "请添加 JSON 文件，或将版面数据来源设为自动识别。",
    errorOutputName: "输出文件名不能包含斜杠。",
    errorConsentRequired: "继续操作前，您必须同意上传和处理文件。",
    preparingTitle: "正在准备文件",
    preparingMessage: "上传前正在检查输入内容。",
    uploadingTitle: "正在发送到服务器",
    uploadingMessage: "浏览器正在直接发送源文件。",
    processingTitle: "服务器正在重建文档",
    processingFast: "正在分析版面并生成可编辑的 DOCX。",
    processingVerified: "正在生成 DOCX，并渲染成图进行视觉校验。",
    finishedTitle: "重建完成",
    finishedMessage: "Word 文档已就绪，下载已经开始。",
    failedTitle: "任务未能完成",
    networkError: "无法连接服务器，请检查地址、运行状态和跨域访问配置。",
    timeoutError: "服务器未在规定时间内响应。",
    serverError: "服务器返回错误 {status}。",
    unknownError: "发生未知错误。",
    resultMeta: "{size} · {quality}模式",
    qualityFastResult: "快速",
    qualityVerifiedResult: "视觉校验",
    visualScore: "视觉相似度 {score}",
    serverDuration: "处理耗时 {seconds} 秒",
  },
  ru: {
    pageTitle: "docreconstruct — Восстановление редактируемых документов",
    skipLink: "Перейти к форме",
    languageLabel: "Язык",
    sourceCodeAria: "Открыть исходный код на GitHub",
    sourceCode: "Исходный код",
    eyebrow: "Открытый веб-клиент",
    title: "Восстановление редактируемых документов",
    subtitle:
      "Объедините содержимое Markdown, оригинал и необязательные данные о макете, чтобы получить редактируемый документ Word, максимально близкий к исходнику.",
    factsAria: "Основная информация",
    factOne: "Файлы не загружаются на GitHub",
    factTwo: "Редактируемый документ DOCX",
    factThree: "Сервер обработки выбираете вы",
    formStep: "Шаги 1–3",
    formTitle: "Подготовка задания",
    noBackendBadge: "Сервер не прилагается",
    serverSection: "Выберите сервер обработки",
    backendLabel: "Адрес сервера",
    required: "Обязательно",
    backendPlaceholder: "https://ваш-сервер.example",
    backendHelp: "Страница обратится к адресу /v1/hybrid на указанном сервере.",
    noPublicBackendTitle: "Публичный сервер обработки не предоставляется.",
    noPublicBackendBody:
      "Страница сможет обработать документ только после настройки совместимого сервера.",
    filesSection: "Добавьте исходные файлы",
    markdownLabel: "Содержимое Markdown",
    markdownHelp: "Текст, формулы и ссылки на изображения",
    chooseFile: "Выбрать файл .md",
    originalLabel: "Исходный PDF или изображение",
    originalHelp: "Образец расположения и размеров элементов",
    chooseOriginal: "Выбрать оригинал",
    jsonLabel: "Данные макета JSON",
    jsonHelp: "Координаты и порядок чтения, полученные средствами OCR",
    chooseJson: "Выбрать файл .json",
    optional: "Необязательно",
    optionsSection: "Выберите способ обработки",
    providerLabel: "Источник данных о макете",
    providerAuto: "Определить автоматически",
    providerHelp: "Указывайте источник, только если знаете, чем был создан файл JSON.",
    qualityLabel: "Уровень проверки",
    qualityFast: "Быстро",
    qualityFastHelp: "Вернуть DOCX сразу после создания",
    qualityVerified: "С проверкой",
    qualityVerifiedHelp: "Отрисовать результат и сравнить визуально",
    advancedTitle: "Дополнительные параметры",
    strictLabel: "Строго проверять JSON",
    strictHelp: "Остановить обработку, если данные о макете некорректны.",
    remoteAssetsLabel: "Разрешить загрузку изображений по ссылкам из Markdown",
    remoteAssetsHelp:
      "Сервер сможет обращаться к внешним адресам, только если оператор явно разрешил это.",
    paddleFallbackLabel: "Разрешить серверу использовать PaddleOCR-VL",
    paddleFallbackHelp: "Работает, только если оператор сервера настроил эту службу.",
    outputNameLabel: "Имя выходного файла",
    outputNamePlaceholder: "редактируемый_документ.docx",
    consentTitle: "Я согласен отправить файлы на выбранный сервер",
    consentBody:
      "Файлы будут загружены на сервер: {backend}. Оператор сервера или поставщик услуги может обрабатывать и хранить их согласно своей политике. Если включить PaddleOCR-VL, сервер сможет переслать файлы в службу OCR, настроенную оператором. Если разрешены изображения по ссылкам, сервер сможет обращаться к адресам из Markdown.",
    consentBackendUnset: "сервер ещё не выбран",
    submit: "Создать документ Word",
    sideAria: "Сведения о задании",
    resultStep: "Состояние",
    progressReadyTitle: "Всё готово",
    progressReadyMessage: "Выберите сервер и исходные файлы, чтобы восстановить документ.",
    progressAria: "Ход восстановления документа",
    downloadAgain: "Скачать ещё раз",
    privacyTitle: "Прозрачные правила конфиденциальности",
    privacyBody:
      "GitHub Pages лишь раздаёт этот статический клиент. Файлы отправляются из браузера прямо на выбранный вами сервер; GitHub не обрабатывает документы, а страница не хранит ключи доступа.",
    costTitle: "Открытый код не означает бесплатные вычисления на GPU",
    costBody:
      "Клиент и проект имеют открытый исходный код. Серверы, электричество, GPU и сторонние службы OCR всё равно могут требовать оплаты — это зависит от настроек оператора.",
    accuracyTitle: "Обязательно проверьте результат",
    accuracyBody:
      "При распознавании и восстановлении макета возможны ошибки в тексте, формулах и расположении. Перед использованием сравните DOCX с оригиналом.",
    footerText: "Веб-клиент сообщества docreconstruct",
    noScript: "Включите JavaScript, чтобы отправить документ на выбранный сервер.",
    selectedFile: "Выбран файл: {name} ({size})",
    errorBackendMissing: "Введите адрес сервера обработки.",
    errorBackendInvalid: "Адрес сервера некорректен. Укажите полный адрес с http:// или https://.",
    errorBackendCredentials: "Не указывайте имя пользователя или пароль в адресе сервера.",
    errorInsecureBackend:
      "Страница HTTPS не может обратиться к этому серверу HTTP. Используйте HTTPS или локальный сервер localhost.",
    errorContentMissing: "Выберите файл Markdown с содержимым документа.",
    errorLayoutMissing: "Выберите исходный PDF или изображение.",
    errorEvidenceType: "Данные о макете должны быть в файле .json.",
    errorProviderWithoutJson:
      "Добавьте файл JSON или оставьте автоматическое определение источника данных.",
    errorOutputName: "Имя выходного файла не должно содержать косую черту.",
    errorConsentRequired:
      "Перед продолжением необходимо согласиться на отправку и обработку файлов.",
    preparingTitle: "Подготовка файлов",
    preparingMessage: "Проверяем исходные данные перед отправкой.",
    uploadingTitle: "Отправка на сервер",
    uploadingMessage: "Браузер напрямую отправляет исходные файлы.",
    processingTitle: "Сервер восстанавливает документ",
    processingFast: "Анализируем макет и создаём редактируемый DOCX.",
    processingVerified: "Создаём DOCX и отрисовываем его для визуальной проверки.",
    finishedTitle: "Документ восстановлен",
    finishedMessage: "Документ Word готов; скачивание уже началось.",
    failedTitle: "Не удалось выполнить задание",
    networkError: "Не удалось связаться с сервером. Проверьте адрес, состояние сервера и настройки CORS.",
    timeoutError: "Сервер не ответил за отведённое время.",
    serverError: "Сервер вернул ошибку {status}.",
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
  provider: document.querySelector("#provider"),
  strictEvidence: document.querySelector("#strict-evidence"),
  remoteAssets: document.querySelector("#remote-assets"),
  paddleFallback: document.querySelector("#paddle-fallback"),
  outputName: document.querySelector("#output-name"),
  consent: document.querySelector("#upload-consent"),
  consentDetail: document.querySelector("#upload-consent-detail"),
  submit: document.querySelector("#submit-button"),
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

function updateConsentCopy() {
  const backend = elements.backend.value.trim() || t("consentBackendUnset");
  elements.consentDetail.textContent = t("consentBody", { backend });
}

function updateSubmitAvailability() {
  elements.submit.disabled = Boolean(activeRequest) || !elements.consent.checked;
  if (activeRequest) elements.submit.dataset.busy = "true";
  else delete elements.submit.dataset.busy;
}

function invalidateConsent() {
  elements.consent.checked = false;
  elements.consent.removeAttribute("aria-invalid");
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

function selectedQuality() {
  return document.querySelector('input[name="quality"]:checked').value;
}

function validateInputs() {
  const endpoint = normalizeEndpoint(elements.backend.value);
  const content = elements.content.files[0];
  const layout = elements.layout.files[0];
  const evidence = elements.evidence.files[0];
  if (!content) throw new Error("errorContentMissing");
  if (!layout) throw new Error("errorLayoutMissing");
  if (evidence && !evidence.name.toLowerCase().endsWith(".json")) {
    throw new Error("errorEvidenceType");
  }
  if (elements.provider.value && !evidence) {
    throw new Error("errorProviderWithoutJson");
  }
  const outputName = elements.outputName.value.trim();
  if (outputName.includes("/") || outputName.includes("\\")) {
    throw new Error("errorOutputName");
  }
  if (!elements.consent.checked) {
    throw new Error("errorConsentRequired");
  }
  return { endpoint, content, layout, evidence, outputName };
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
    elements.outputName,
    elements.consent,
  ].forEach((element) => element.removeAttribute("aria-invalid"));
}

function focusInvalidField(errorKey) {
  const targetByError = {
    errorBackendMissing: elements.backend,
    errorBackendInvalid: elements.backend,
    errorBackendCredentials: elements.backend,
    errorInsecureBackend: elements.backend,
    errorContentMissing: elements.content,
    errorLayoutMissing: elements.layout,
    errorEvidenceType: elements.evidence,
    errorProviderWithoutJson: elements.provider,
    errorOutputName: elements.outputName,
    errorConsentRequired: elements.consent,
  };
  const target = targetByError[errorKey];
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
    showError(t(key));
    focusInvalidField(key);
    return;
  }

  const quality = selectedQuality();
  const options = {
    quality,
    strict_evidence: elements.strictEvidence.checked,
    remote_assets: elements.remoteAssets.checked,
    use_paddleocr_vl: elements.paddleFallback.checked,
  };
  if (elements.provider.value) options.evidence_provider = elements.provider.value;
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
elements.backend.addEventListener("input", invalidateConsent);
elements.paddleFallback.addEventListener("change", invalidateConsent);
elements.remoteAssets.addEventListener("change", invalidateConsent);
elements.consent.addEventListener("change", () => {
  if (elements.consent.checked) elements.consent.removeAttribute("aria-invalid");
  updateSubmitAvailability();
});
elements.form.addEventListener("submit", submitJob);
window.addEventListener("beforeunload", () => {
  if (activeDownloadUrl) URL.revokeObjectURL(activeDownloadUrl);
});

applyLanguage(currentLanguage);
