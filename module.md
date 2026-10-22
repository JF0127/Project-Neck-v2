# Project-Neck-v2

## 模块

### audio · 声音收听与播放
概要：把听到的声音变成文字，再读出回答。
详细：接收麦克风声音，识别说话片段并转换为中文文字。接收回答文字，取得合成声音并播放，同时提供声音开始和结束的时间。

### audio/capture · 声音采集
概要：持续收集麦克风传来的声音。
详细：通过系统音频服务读取单声道声音，整理成固定长度的小段。根据会话状态开始或停止采集，重新开始时清空旧声音，队列满时丢弃新到的小段。

### audio/segment · 说话片段划分
概要：判断什么时候开始和停止说话。
详细：接收连续声音小段，使用本地模型判断其中是否有人声。达到持续说话或静音条件时输出开始、结束事件，并输出包含开头预留声音的识别材料。

### audio/recognize · 中文声音识别
概要：把说话声音转换为中文文字。
详细：接收本轮说话声音，转换为浮点采样后交给本地中文识别模型。说话结束时完成剩余识别，向会话组织输出最终文字。

### audio/synthesize · 回答声音合成
概要：把回答文字换成可以播放的声音。
详细：接收按顺序排队的回答短句，通过远程声音服务逐段取得声音。提前建立首段连接，解析收到的声音报文，并向播放模块交付未压缩声音和合成进度。

### audio/playback · 声音播放与动作对时
概要：播放回答，并在合适时刻触发动作。
详细：接收声音小段和段落信息，按播放设备的实际时间安排输出，缺少声音时补静音而不丢弃排队声音。根据段落开始、末段进度和播放结束时间触发表情、转头、强调与待机动作，并处理取消播放和尾部静音。

### audio/models · 声音模型文件
概要：保存辨认说话声音所需的数据。
详细：提供本地人声判断模型和由启动参数或环境指定的中文识别模型数据。声音处理启动时读取这些数据，分别建立说话判断能力和中文识别能力。

### dialogue · 对话组织与交互
概要：安排倾听、回答和暂停的先后顺序。
详细：接收键盘文字或声音识别结果，组织回答生成、声音合成、播放和动作触发。维护会话状态，处理人工中断，并保存每轮内容、状态变化和耗时。

### dialogue/control · 会话状态控制
概要：让使用者切换倾听和等待状态。
详细：接收单键操作和本地网页请求，查询或切换等待、聆听状态，也接受会话自动报告的说话状态。人工切换会使旧一轮处理失效并触发中断，同时记录状态变化。

### dialogue/turn · 单轮会话组织
概要：把一次输入组织成完整的回答过程。
详细：接收文字、说话事件和状态变化，安排识别收尾、回答生成、声音播放及对应动作。说话模式在机器人回答期间停止收音，取消时清理排队任务；结束时整理本轮记录与耗时，空闲后恢复默认姿态。

### dialogue/response · 回答生成与整理
概要：整理回答并保留最近的对话。
详细：接收用户文字、最近十轮已完成对话、当前时间和可用表情名称，向远程回答服务提交请求。处理搜索要求，等待最终回答完整结束后按行整理短句，输出文字、表情、强度和强调标记。

### dialogue/tools · 时间与搜索资料
概要：为回答提供当前时间和网上资料。
详细：向回答组织提供上海时区的当前日期、时间和星期。接收搜索词后调用远程搜索服务，返回标题、日期、摘要或失败说明，并记录搜索耗时。

### dialogue/turn_logs · 单轮对话日志文件
概要：保存每轮说了什么、做了什么。
详细：接收整理后的本轮记录，保存输入文字、回答段落、声音时间、动作事件、搜索信息和错误。每轮使用独立的文字文件，后续空闲动作更新可重写同一记录，离线分析按需读取其中事件。

### dialogue/state_logs · 状态变化日志文件
概要：保存倾听和说话状态的变化经过。
详细：接收每次有效状态变化，逐条追加变化前后状态、来源、时间和是否中断回答。记录同时保留日历时间与单调时间，作为独立的状态事件文件。

### dialogue/timing_logs · 会话耗时日志文件
概要：保存每轮回答花费的时间。
详细：接收从本轮时间点计算出的各阶段耗时，追加保存识别、回答、合成、播放和搜索的时间统计。每轮只记一次，并保留用户文字、段落数量和是否触发思考动作。

### motion · 动作编排与姿态生成
概要：把说话情绪和动作安排变成头部运动。
详细：接收会话动作与声音播放时间，生成平滑的头部方向变化并发往电机控制。也提供独立表情演示和已有动作数据的模拟发送，并保存发送记录。

### motion/schedule · 会话动作安排
概要：决定头部在什么时刻做什么动作。
详细：接收表情、转头、强调、状态重置和说话起止事件，在独立进程中安排生效时间。结合动作生成结果、待机摆动和基础低头角度，持续发送提前安排的头部姿态，并记录执行或跳过的原因。

### motion/render · 连续姿态生成
概要：让头部动作连贯地过渡。
详细：接收带时间的表情和动作事件，读取动作参数，生成连续的侧倾、俯仰和转头角度。处理姿态切换、点头、摇头、自动返回与角度限制，向实时安排或独立演示返回采样结果。

### motion/demo · 动作演示与模拟发送
概要：按预设顺序展示和发送头部动作。
详细：根据表情名称与演示参数构造事件，取得采样结果后按时间发送姿态。另一条路径读取已有动作数据，重新采样并分段发送，可加入计算等待、随机延迟和停顿。

### motion/config · 表情与会话配置文件
概要：保存表情幅度和说话动作的设置。
详细：保存表情姿态、动作幅度、过渡时间、待机摆动、播放等待和会话触发条件。向动作安排、姿态生成、演示及回答整理提供参数；当前配置中倾听停顿点头和收尾确认点头的幅度为零。

### motion/trajectory_files · 动作轨迹文件
概要：保存可重复播放的一组头部动作。
详细：提供由时间和三个头部角度组成的表格数据。电机回放读取从零开始且递增的相对时间，模拟发送读取后重新采样并换算为绝对执行时间。

### motion/logs · 动作发送日志文件
概要：保存动作安排和实际发送的姿态。
详细：接收动作事件和每次发送的姿态，保存请求时间、生效时间、播放时间、角度及执行结果。发送记录同时保存单调时间与日历时间，供离线分析对齐电机记录。

### neck · 头部与电机控制
概要：让三个电机按目标带动头部运动。
详细：接收人工命令、动作文件或持续到来的头部姿态，换算并限制电机目标。按固定节奏发送控制报文，接收设备反馈，维护运行状态并保存控制过程。

### neck/command · 人工控制入口
概要：接收使用者对头部和电机的操作。
详细：接收终端命令，安排头部定位、往返运动、阶跃动作、文件回放、持续接收、停止和释放。也提供电机编号、零点、位置、速度及制动操作，向使用者返回状态或错误信息。

### neck/trajectory · 目标接收与跟随
概要：按时间跟随目标，缺少目标时停稳。
详细：接收带时间的头部姿态、文件回放数据或人工运动要求，进行补点、衔接和等待、跟随、减速、保持等状态切换。丢弃过期输入，结合运动范围与速度限制生成电机目标，并利用反馈更新当前姿态。

### neck/kinematics · 头部与电机角度换算
概要：换算头部方向和电机转角。
详细：接收头部姿态或三个电机角度，结合机械尺寸、方向、零位和活动范围完成双向换算。输出角度、可达性和余量判断，供运行控制及离线分析使用。

### neck/drive · 电机指令与反馈处理
概要：把电机目标变成可发送的指令。
详细：接收电机位置、速度及控制参数，加入可选的周期力矩补偿和摩擦补偿，换算单位后编码为控制报文。接收设备反馈报文，整理位置、速度、电流、温度和错误状态，供控制与日志使用。

### neck/bus · 电机通信交换
概要：在电脑和电机转接板之间传递消息。
详细：通过专用网络接口连接转接板，持续交换电机控制和反馈报文，并处理人工查询。维护连接是否可用的状态，进行异常报告和从站恢复，把收到的数据交给反馈处理。

### neck/config · 电机配置文件
概要：保存电机运行设置和角度对应关系。
详细：保存运行频率、控制强度、速度限制、端口、电机编号、方向、零位和机械范围。另保存按电机及转动方向划分的力矩补偿表和原始表，向运行控制、角度换算及离线工具提供参数。

### neck/logs · 电机运行日志文件
概要：保存电机指令和实际反馈的变化。
详细：接收控制周期记录与有效反馈记录，分别保存主运行表格和反馈表格。记录包含时间、头部目标、电机指令、反馈电流、状态和补偿力矩，由后台写入供离线读取。

### analysis · 离线分析与项目浏览
概要：解释已有运行记录并展示项目组成。
详细：接收已保存的对话、动作、电机记录和配置，分析运动表现或生成补偿数据。还提供不连接硬件的范围判断工具，以及读取项目模块说明的只读网页。

### analysis/run_analysis · 运行记录分析
概要：从记录中找出延迟、停顿和动作差异。
详细：读取电机记录，计算运行周期、跟随误差、延迟、速度、电流和温度统计。也可结合动作发送及对话事件对齐时间，报告疑似跳动、缓冲不足和跟随偏差，并按选项保存图像。

### analysis/calibration · 离线范围判断
概要：根据已有设置计算头部活动范围。
详细：读取电机对应关系、零位、边界及参考范围，调用角度换算计算零位、单轴范围和方形边界。输出一致或不一致的文字结果，不连接电机，也不修改标定数据。

### analysis/ripple_builder · 力矩补偿建表
概要：从电机记录中整理减小抖动的数据。
详细：读取一份或多份反馈记录，提取正反向匀速运动中的电流波动并换算为周期力矩表。也可读取已有原始表，只保留指定周期成分，将结果和可选原始表写入电机配置。

### analysis/report_files · 分析图像文件
概要：保存对照头部动作变化的图片。
详细：接收运行记录分析生成的图像，保存发送姿态、控制目标、反馈姿态、缓冲余量和异常位置。每次启用图像输出的分析独立生成文件，关闭图像输出时不写入。

### analysis/structure_view · 项目结构浏览
概要：用网页查看模块和它们之间的联系。
详细：提供只读网页和模块说明内容，网页定期重新读取并绘制模块关系。接收展开模块、选择连接和筛选数据类型的操作，显示对应的概要、详细描述和数据流。

### analysis/structure_files · 项目结构展示文件
概要：保存模块说明和网页展示内容。
详细：提供描述模块与数据流的文字文件，以及负责浏览交互的网页内容。只读浏览服务按请求读取这些文件，不替使用者修改它们。

### external · 外部设备与服务（外部）
概要：提供声音、回答和电机运动的外部支持。
详细：包括本项目使用的声音设备、电机设备、远程服务与操作客户端。接收本地程序发出的请求或指令，返回声音、文字、操作和设备状态。

### external/audio_device · 麦克风与扬声器（外部）
概要：让系统听到使用者并读出回答。
详细：麦克风及系统音频服务向采集模块提供声音。播放设备接收回答声音并输出，同时提供播放时钟和设备延迟信息。

### external/language_service · 回答服务（外部）
概要：根据对话内容生成中文回答。
详细：接收用户问题、历史对话、当前时间和输出要求，返回分段格式的回答内容。需要网上信息时返回搜索要求，接收搜索结果后继续回答。

### external/search_service · 网络搜索服务（外部）
概要：按关键词查找网上资料。
详细：接收搜索词和期望结果数量，返回搜索条目的标题、日期、摘要等信息。本地工具整理这些结果供回答使用。

### external/speech_service · 声音合成服务（外部）
概要：把文字读成指定声音。
详细：接收回答文字、音色和声音参数，返回未压缩的单声道声音小段。另返回结束或错误事件，供本地判断本段合成是否完成。

### external/bridge · 电机通信转接板（外部）
概要：在电脑与电机之间转交控制消息。
详细：接收电脑发来的电机报文，通过电机通信通道转发。收集电机回复并放入返回报文，供电脑持续读取。

### external/motors · 三个电机与头部机构（外部）
概要：带动头部转动并报告电机状态。
详细：接收位置、速度、力矩、参数查询或制动指令，驱动头部机构运动。返回电机自身的角度、速度、电流和状态，头部姿态由本地根据电机角度换算。

### external/client · 终端与网页客户端（外部）
概要：让使用者输入内容并查看运行结果。
详细：通过终端输入对话文字、控制命令和启动选项，或通过网页请求切换状态、查看项目组成。接收程序输出的回答、状态、分析文字和网页内容。

## 数据流
- external/audio_device -> audio/capture : microphone_pcm_source | 麦克风声音 | 波形 | 实时 | 16 kHz、单声道、16 位有符号小端 PCM；采集端按 20 ms 读取，每段 320 个采样、640 字节，约 50 段每秒。
- dialogue/turn -> audio/capture : capture_state_command | 收音启停 | 文本 | 实时 | 开始或停止采集的控制事件，无物理单位；随聆听状态、回答开始和人工切换触发，无固定频率。
- audio/capture -> audio/segment : microphone_pcm_captured | 待判断声音 | 波形 | 实时 | 转交的 PCM 字节段，格式仍为 16 kHz、单声道、16 位小端；每段 20 ms，正常约 50 段每秒。
- audio/models -> audio/segment : speech_detector_weights | 人声模型 | 数值 | 配置 | 本地二进制模型数据，无物理单位；声音判断启动时读取一次，在 CPU 上处理每个 512 采样的判断窗。
- audio/models -> audio/recognize : speech_recognizer_weights | 识别模型 | 数值 | 配置 | 本地中文识别模型及配套数据，无统一物理单位；识别模块启动时加载，内部识别块长设为 1 s。
- audio/segment -> dialogue/turn : speech_boundary_events | 说话起止 | 文本 | 实时 | 开始、结束事件及静音采样数；按输入帧处理后返回状态，判断窗为 512 个采样、32 ms，边界事件按说话过程触发。
- audio/segment -> dialogue/turn : speech_pcm_selected | 选中声音 | 波形 | 实时 | 16 kHz、16 位小端 PCM；开始事件一次带入最多约 400 ms 的预留声音，随后随有效输入帧返回声音，结束帧也保留。
- dialogue/turn -> audio/recognize : speech_pcm_forwarded | 识别声音 | 波形 | 实时 | 转交本轮选中的 PCM，识别前换为归一化浮点采样；采样率 16 kHz，按收到的声音段推送，停止或切换状态后不再接收旧轮结果。
- dialogue/turn -> audio/recognize : recognition_lifecycle | 识别起止 | 文本 | 实时 | 新建本轮识别、结束收尾和清空状态的事件，无物理单位；每段说话或人工中断时触发，无固定频率。
- audio/recognize -> dialogue/turn : recognized_text_final | 识别文字 | 文本 | 实时 | 去除首尾空白的中文字符串，无物理单位；每个完成的说话片段返回一次最终结果，空结果不进入回答生成。
- external/client -> dialogue/turn : keyboard_turn_text | 键盘文字 | 文本 | 实时 | 使用者输入的字符串，无物理单位；文字模式每提交一行产生一次输入，空白行不生成回答。
- external/client -> dialogue/control : conversation_control_request | 状态操作 | 文本 | 实时 | 单键事件或 HTTP 请求，状态正文为 JSON，无物理单位；按人工操作发送，默认本地端口 8770，无固定频率。
- dialogue/control -> external/client : conversation_control_response | 当前状态 | 文本 | 实时 | 包含状态或错误信息的 JSON 及终端提示，无物理单位；每次状态查询、有效切换或错误请求产生响应。
- dialogue/control -> dialogue/turn : conversation_state_changed | 状态变化 | 文本 | 实时 | 包含新旧状态、操作来源及修订号的状态信息，无物理单位；人工切换或退出时触发取消，状态变化无固定频率。
- dialogue/turn -> dialogue/control : conversation_state_automatic | 自动切换 | 文本 | 实时 | 聆听与说话状态的自动变更事件，无物理单位；识别到有效文字以及回答播放结束时触发。
- dialogue/turn -> dialogue/response : user_turn_forwarded | 用户问题 | 文本 | 实时 | 本轮输入文字及记录上下文，无物理单位；每轮有效文字输入提交一次。
- dialogue/tools -> dialogue/response : current_datetime_context | 当前时间 | 文本 | 实时 | 上海时区的日期、时分秒与星期字符串，时间精度为秒；每次远程回答请求前生成一次。
- motion/config -> dialogue/response : expression_names_file | 可用表情 | 文本 | 文件 | 从 JSON 文件读取的表情名称列表，无物理单位，并加入无表情选项；每轮回答生成开始时重新读取。
- dialogue/response -> external/language_service : language_request_context | 回答请求 | 文本 | 实时 | 包含当前输入、最近最多十轮对话、时间、表情要求及搜索工具说明的 JSON 请求，无物理单位；每轮至少一次，工具结果返回后可继续请求。
- external/language_service -> dialogue/response : language_response_events | 回答事件 | 文本 | 实时 | 回答增量、工具调用、完成或失败事件，无物理单位；随网络流到达，无固定频率，本地先积累最终回答正文。
- dialogue/response -> dialogue/tools : search_query_requested | 搜索词 | 文本 | 实时 | 搜索词字符串，无物理单位；每轮最多执行两次搜索，工具端去空白并截取前 128 个字符。
- dialogue/tools -> external/search_service : search_query_forwarded | 搜索请求 | 文本 | 实时 | 包含搜索词和结果数量的 JSON，默认请求 4 条，无物理单位；每次搜索发出一次，超时限制为 5 s。
- external/search_service -> dialogue/tools : search_result_entries | 搜索条目 | 文本 | 实时 | 包含标题、日期、摘要等内容的 JSON 结果，无物理单位；每次搜索返回一批，数量由服务实际结果决定。
- dialogue/tools -> dialogue/response : search_result_summary | 搜索资料 | 文本 | 实时 | 将结果整理为标题、发布日期和摘要的字符串，失败时返回失败说明，无物理单位；每次搜索完成后交付一次。
- dialogue/response -> dialogue/turn : search_started_notice | 搜索开始 | 文本 | 实时 | 搜索开始事件，无物理单位；工具调用出现时通知会话，本轮首次通知可加入固定等待短句并触发思考动作。
- dialogue/response -> dialogue/turn : reply_segments_parsed | 回答短句 | 文本 | 实时 | 包含文字、表情、0 至 1 强度和布尔强调标记的结构化短句；最终响应完整结束且无待处理工具调用后逐段交付，每段最多 20 字，整轮接受的正文最多 60 字。
- dialogue/turn -> audio/synthesize : synthesis_work_queued | 合成任务 | 文本 | 实时 | 连接预热、段落文字和队列结束标记，无物理单位；每轮启动预热，每段入队一次，当前预热等待上限为 1 s。
- dialogue/turn -> audio/playback : playback_segment_metadata | 段落信息 | 文本 | 实时 | 共享的文字、表情、强度、强调和末段标记，无物理单位；每段到达和段落边界确定时更新，末段需等完整合成后开始播放。
- audio/synthesize -> external/speech_service : synthesis_request_packet | 合成请求 | 数值 | 实时 | 带二进制协议头的 JSON 请求，包含文字、音色和 16 kHz PCM 输出设置；每段请求一次，无固定发送频率。
- external/speech_service -> audio/synthesize : synthesized_pcm_received | 合成声音 | 波形 | 实时 | 事件报文承载的 16 kHz、单声道、16 位小端未压缩 PCM；分段长度和到达频率由服务决定，结束事件不含可播放声音。
- audio/synthesize -> audio/playback : synthesized_pcm_decoded | 待播放声音 | 波形 | 实时 | 解包后的 16 位整数采样数组及对应段落信息，采样率 16 kHz；每收到一个有效声音包入队一次，网络到达频率不固定。
- audio/playback -> external/audio_device : playback_pcm_output | 扬声器声音 | 波形 | 实时 | 16 kHz、单声道、16 位整数声音；每次输出 160 个采样、10 ms，约 100 次每秒，回答末尾追加 0.3 s 静音。
- external/audio_device -> audio/playback : audio_device_clock | 播放时钟 | 曲线 | 实时 | 播放缓冲实际输出时刻和设备延迟，单位 s；输出回调约每 10 ms 提供时间，启动时建立与单调时钟的对应关系。
- dialogue/turn -> audio/synthesize : synthesis_cancel_event | 取消合成 | 文本 | 实时 | 取消标记与队列清理事件，无物理单位；人工中断、退出或本轮失败时触发，无固定频率。
- dialogue/turn -> audio/playback : playback_cancel_event | 取消播放 | 文本 | 实时 | 停止标记与播放流中止事件，无物理单位；人工中断、退出或处理失败时触发，并清空尚未播放的声音。
- audio/synthesize -> dialogue/turn : synthesis_timing_updates | 合成进度 | 曲线 | 实时 | 请求、首包、末包和结束的单调时间戳，以及采样数和声音时长；时间单位 s，随请求、声音包和结束事件更新。
- audio/playback -> dialogue/turn : playback_timing_updates | 播放进度 | 曲线 | 实时 | 段落开始、结束、已写入时长及播放排空或中止时间，单位 s；随 10 ms 输出回调和播放状态更新。
- dialogue/response -> dialogue/turn : language_timing_updates | 回答耗时点 | 曲线 | 实时 | 请求、首个文字、首段和最终完成的单调时间戳，单位 s；每轮在相应事件发生时更新，搜索记录另含次数和耗时。
- dialogue/turn -> motion/schedule : conversation_action_events | 会话动作 | 文本 | 实时 | 表情、重置、点头和思考事件，携带单调时间 s、无量纲强度及保持时长 s；按说话边界、等待和空闲触发，当前零幅度点头不发送动作。
- audio/playback -> motion/schedule : playback_action_events | 播放动作 | 文本 | 实时 | 与播放时刻关联的表情、移开视线、姿态切换、强调、回看及待机摆动开关事件；时间单位 s，按段落边界和末段进度触发，表情请求可提前 0.25 s，短句姿态切换当前提前 0.15 s。
- dialogue/turn -> dialogue/turn_logs : dialogue_record_json | 单轮记录 | 文本 | 文件 | JSON 记录，包含文字、事件、错误、单调时间戳及耗时，时间单位 s；每轮结束或中断时写入，空闲姿态更新时可重写同一文件。
- dialogue/control -> dialogue/state_logs : state_transition_jsonl | 状态记录 | 文本 | 文件 | 逐行 JSON，包含日历时间字符串、单调秒数、前后状态及来源；每次实际状态变化追加一行，无固定频率。
- dialogue/turn -> dialogue/timing_logs : dialogue_timing_csv | 耗时记录 | 曲线 | 文件 | CSV 保存各阶段耗时 s、段落数量和思考标记；每轮首次整理日志时追加一行，耗时保留到毫秒，不重复统计后续重写。
- motion/config -> motion/schedule : conversation_motion_settings | 动作设置 | 数值 | 配置 | JSON 参数包含姿态 deg、时间 s、强度比例及开关；动作安排启动时读取一次，当前基础低头为 3 deg，待机左右摆动幅度 8 deg、基准周期 8 s。
- motion/schedule -> dialogue/turn : conversation_settings_forwarded | 会话设置 | 数值 | 配置 | 转交已加载的会话参数，包含结束静音 0.4 s、思考等待 0.8 s、首播等待 0.05 s 和请求播放延迟 0.15 s；启动时取得，随后用于各轮。
- dialogue/turn -> audio/segment : speech_end_silence_setting | 结束静音 | 数值 | 配置 | 浮点静音阈值，当前为 0.4 s，初始化时换算为毫秒和采样数；声音判断实例启动时传入一次。
- motion/schedule -> audio/playback : playback_settings_forwarded | 播放设置 | 数值 | 配置 | 已加载的首播等待、请求输出延迟、动作提前量及末段回看比例；时间单位 s、比例无量纲，每轮播放流初始化时取得当前会话配置。
- motion/config -> motion/render : expression_library_file | 姿态规则 | 数值 | 文件 | JSON 参数包含三个姿态角 deg、动作幅度 deg、过渡时间 s 和次数；每次生成姿态时重新读取，实时会话通常约 30 次每秒，离线演示按批次读取。
- motion/schedule -> motion/render : scheduled_motion_events | 已排动作 | 文本 | 实时 | 带时间的表情和动作事件列表，时间换成相对本次求值点的秒数，角度为 deg；正常每秒求值 30 次，包含尚未生效的事件。
- motion/render -> motion/schedule : rendered_head_pose | 基础姿态 | 曲线 | 实时 | 三个浮点头部角度，依次为侧倾、俯仰和转头，单位 deg；约 30 Hz 返回求值结果，生成阶段分别限制在正负 8、8、30 deg。
- motion/schedule -> neck/trajectory : head_pose_udp_live | 会话姿态 | 曲线 | 实时 | UDP 文本每行包含执行时刻和三个角度，时间为本机单调时钟 s，角度为 deg；正常 30 Hz 发往本地 9870 端口，目标时刻提前约 0.15 s，错过发送节拍时跳过而不补发突发包。
- motion/schedule -> motion/logs : motion_samples_csv | 姿态发送记录 | 曲线 | 文件 | CSV 保存发送单调时间、目标时间、日历秒数及三个角度 deg；每次实际发送后写一行，正常约 30 行每秒。
- motion/schedule -> motion/logs : motion_events_csv | 动作事件记录 | 文本 | 文件 | CSV 事件行包含动作名称、请求与生效时间、播放时间、提前量、状态及原因；时间单位 s，按事件安排、执行或跳过时写入。
- external/client -> motion/demo : demonstration_options | 演示选项 | 数值 | 配置 | 表情名称、强度、方向、保持时间、端口或模拟发送选项；时间单位 s、频率单位 Hz，其余按字符串或无量纲值处理，每次工具启动时读取一次。
- motion/config -> motion/demo : demonstration_expression_settings | 演示设置 | 数值 | 配置 | JSON 表情姿态和动作参数，角度为 deg、时长为 s；表情演示启动时读取一次，用于构造整段事件顺序。
- motion/demo -> motion/render : demonstration_events | 演示事件 | 文本 | 实时 | 相对时间 s、表情名称、无量纲强度及方向、保持时间 s 组成的事件列表；每次表情演示批量提交一次。
- motion/render -> motion/demo : demonstration_pose_samples | 演示采样 | 曲线 | 实时 | 浮点数组，每行包含相对时间 s 和三个角度 deg；表情演示按 30 Hz 时间网格一次生成整段结果。
- motion/trajectory_files -> motion/demo : trajectory_csv_simulation | 模拟动作文件 | 曲线 | 文件 | CSV 含相对时间 s 和三个头部角度 deg；每次模拟启动读取一次，按选项重新采样，默认生成 30 Hz 姿态点。
- motion/demo -> neck/trajectory : head_pose_udp_demo | 演示姿态 | 曲线 | 实时 | 与会话发送相同的 UDP 行格式，时间为单调时钟 s、角度为 deg；表情演示以 30 Hz 提前 0.3 s 发送，文件模拟默认按 0.2 s 一段打包 30 Hz 样本，并加入设定的等待和抖动。
- external/client -> neck/command : motor_console_request | 电机操作 | 文本 | 实时 | 终端命令及参数，角度 deg、时间 s、频率 Hz、速度 deg/s、加速度 deg/s²；按使用者提交触发，无固定频率。
- neck/command -> neck/trajectory : neck_motion_request | 头部任务 | 文本 | 实时 | 定位、往返、阶跃、回放、持续接收、停止与释放要求，附带控制参数；角度 deg、时间 s、速度 deg/s、加速度 deg/s²，通常每条命令提交一次后台任务。
- motion/trajectory_files -> neck/trajectory : trajectory_csv_playback | 回放动作文件 | 曲线 | 文件 | CSV 每行包含相对时间 s 和三个角度 deg，时间从零开始且严格递增；每次回放读取一次，输入采样间隔由文件决定，必要时先生成到首姿态的过渡。
- neck/config -> neck/command : motor_command_defaults | 命令默认值 | 数值 | 配置 | JSON 默认参数，包含端口、增益、时间 s、频率 Hz、速度 deg/s 和加速度 deg/s²；首次取得默认值时读取，人工选项可覆盖本次命令参数。
- neck/config -> neck/trajectory : controller_runtime_settings | 控制设置 | 数值 | 配置 | JSON 运行参数；默认控制频率 500 Hz、接收端口 9870、日志开启，速度和加速度限制当前为零即不限，控制实例初始化时取得。
- neck/config -> neck/kinematics : motor_calibration_settings | 角度对应设置 | 数值 | 配置 | JSON 电机编号、方向、零位、机械端点和余量；角度单位 deg，机械端点各内收 5 deg、模型最小余量 30 deg，换算模型初始化时读取。
- neck/trajectory -> neck/kinematics : head_pose_inverse_input | 目标角度换算 | 曲线 | 实时 | 三个浮点头部角度，单位 deg；默认 500 Hz 控制步内按需要多次求解，用于目标换算和边界限制。
- neck/kinematics -> neck/trajectory : motor_pose_inverse_result | 电机角度结果 | 曲线 | 实时 | 三个电机目标角度及余量 deg，另含可达、可用标记；每次目标换算返回一次，控制步内可能多次返回。
- neck/trajectory -> neck/kinematics : motor_pose_forward_input | 电机角度换算 | 曲线 | 实时 | 三个电机绝对角度，单位 deg；来自最新反馈、初始位置或人工定位，运行中通常每个 500 Hz 控制步求值。
- neck/kinematics -> neck/trajectory : head_pose_forward_result | 当前头部姿态 | 曲线 | 实时 | 根据电机角度计算的三个头部角度，单位 deg，不是独立姿态传感器测量；随换算请求返回，运行中通常约 500 Hz。
- neck/trajectory -> neck/drive : motor_targets_degrees | 电机目标 | 曲线 | 实时 | 三个电机的位置 deg、速度 deg/s，以及控制增益和补偿选项；默认 500 Hz 更新，位置间差分用于计算电机速度。
- neck/trajectory -> neck/drive : drive_session_events | 电机会话事件 | 文本 | 实时 | 初始位置、速度、电流查询及释放要求，无统一物理单位；随会话启动或人工释放触发，查询按位置、速度、电流顺序进行。
- neck/config -> neck/drive : ripple_lookup_file | 力矩补偿表 | 数值 | 文件 | 每个电机正反方向各 500 个浮点力矩值，单位 N·m，覆盖 10 deg 周期、节点间隔 0.02 deg；当前补偿系数默认为零，首次启用非零补偿时读取。
- neck/drive -> neck/bus : motor_command_packets | 电机控制报文 | 数值 | 实时 | 编码后的三电机控制报文，单电机有效载荷 8 字节；编码前位置为 rad、速度 rad/s、力矩 N·m，默认 500 Hz 更新最新目标，由通信循环重复交换。
- neck/bus -> neck/drive : motor_feedback_packets | 电机反馈报文 | 数值 | 实时 | 转接板返回的二进制电机帧及对应已发送指令，单电机数据区最多 8 字节；通信循环 1000 Hz，只有有效交换才交付新反馈，具体字段由回复类型决定。
- neck/drive -> neck/trajectory : motor_feedback_degrees | 电机状态反馈 | 曲线 | 实时 | 三电机位置 deg、速度 deg/s、电流 A、温度 °C、错误码及更新时间；按有效反馈更新，控制循环默认每秒 500 次读取最新快照。
- neck/bus -> neck/trajectory : motor_bus_availability | 通信可用状态 | 文本 | 实时 | 连接可用标记、工作计数及从站状态，无物理单位；通信循环按 1000 Hz 更新，控制步读取后决定是否继续运行。
- neck/command -> neck/bus : manual_motor_packets | 原始电机操作 | 数值 | 实时 | 电机编号、零点、位置、速度、制动或查询的二进制报文；位置命令用 deg，速度字段依模式为 rpm 或 0.1 rpm，电流限制为 0.1 A，按人工命令入队或发起查询。
- neck/bus -> neck/command : manual_motor_reply | 原始操作回复 | 数值 | 实时 | 编号或参数查询的原始回复帧，角度查询解码结果为 deg；每次有效查询返回一次，查询等待上限 1 s。
- neck/trajectory -> neck/command : neck_status_result | 头部控制状态 | 文本 | 实时 | 当前电机角度、头部角度、余量 deg、可用标记及错误说明；按状态查询或后台任务结果提供，无固定频率。
- neck/command -> external/client : motor_console_response | 电机操作结果 | 文本 | 实时 | 命令接受、状态、可用动作名称或错误的终端文字；角度以 deg 显示，按命令和异常事件输出，无固定频率。
- neck/bus -> external/bridge : motor_process_data_out | 发往转接板 | 数值 | 实时 | EtherCAT 输出数据为 86 字节的打包结构，含两个头字段和六个电机槽位；运行交换频率 1000 Hz，头部控制使用第一组的前三个槽位。
- external/bridge -> neck/bus : motor_process_data_in | 转接板回复 | 数值 | 实时 | EtherCAT 输入数据包含至少 86 字节的反馈结构；电脑按 1000 Hz 交换读取，工作计数不足时不作为新反馈使用。
- external/bridge -> external/motors : motor_can_commands_forwarded | 转交电机指令 | 数值 | 实时 | 转发的 CAN 电机编号、长度和最多 8 字节载荷，头部使用编号 1、2、3；随下行报文转交，设备侧独立发送频率未在本地代码中设定。
- external/motors -> external/bridge : motor_can_feedback_returned | 电机设备回复 | 数值 | 实时 | CAN 回复包含角度、速度、电流、温度、状态或查询值，具体编码依回复类型；随设备应答到达，独立反馈频率未在本地代码中设定。
- neck/trajectory -> neck/logs : controller_command_csv | 控制周期记录 | 曲线 | 文件 | 主 CSV 含时间 s、头部及电机角度 deg、速度 deg/s、电流 A、温度 °C、缓冲 ms 和编码后指令；日志开启时每控制步入队，默认 500 Hz，后台约每 20 ms 批量处理。
- neck/drive -> neck/logs : motor_feedback_csv | 反馈周期记录 | 曲线 | 文件 | 反馈 CSV 含日历秒数、电机角度 deg、速度 deg/s、电流 A、实际生效指令 rad、rad/s、N·m 和补偿力矩；控制报文开始后每次有效交换记录，通常对应 1000 Hz，后台批量写入。
- dialogue/turn_logs -> analysis/run_analysis : dialogue_events_file | 对话事件读取 | 文本 | 文件 | 已保存的 JSON，包含说话边界、动作事件和播放时间，时间单位为单调时钟 s；关联分析按需读取指定一轮或多轮记录。
- motion/logs -> analysis/run_analysis : motion_samples_file | 发送记录读取 | 曲线 | 文件 | 已保存的 CSV，包含约 30 Hz 姿态 deg、发送和目标时间 s 及动作事件；每次关联分析读取一次，用日历时间与单调时间关系对齐电机记录。
- neck/logs -> analysis/run_analysis : motor_session_files | 电机记录读取 | 曲线 | 文件 | 主运行与反馈 CSV，包含时间 s、角度 deg、速度 deg/s、电流 A、温度 °C 和缓冲 ms；每次分析批量读取，原始记录通常分别对应 500 Hz 与 1000 Hz。
- analysis/run_analysis -> neck/kinematics : logged_motor_angles | 记录角度换算 | 曲线 | 实时 | 从日志取得的三列电机角度数组，单位 deg；离线分析运行时按批次换算，保留原记录时间，不按真实时间逐点等待。
- neck/kinematics -> analysis/run_analysis : reconstructed_head_angles | 还原头部姿态 | 曲线 | 实时 | 与输入记录对应的头部侧倾、俯仰和转头数组，单位 deg；随离线换算批次返回，用于误差、速度和停顿分析。
- analysis/run_analysis -> analysis/report_files : motion_comparison_png | 动作对照图片 | 数值 | 文件 | PNG 二进制图像，图中角度单位 deg、时间 s、缓冲 ms；每次启用绘图的关联分析保存一张，关闭绘图时不产生文件。
- analysis/run_analysis -> external/client : run_analysis_report | 运行分析结果 | 文本 | 实时 | 终端文字，包含误差 deg、延迟 ms、速度 deg/s、加速度 deg/s²、电流 A、温度 °C 和异常时间；每次离线分析按结果输出，无固定频率。
- neck/config -> analysis/calibration : calibration_reference_settings | 范围判断设置 | 数值 | 配置 | JSON 标定及参考参数，角度单位 deg，当前扫描步长 0.05 deg、参考范围容差 0.2 deg；离线工具启动时读取一次。
- analysis/calibration -> neck/kinematics : calibration_scan_points | 待算范围点 | 数值 | 实时 | 零姿态、单轴扫描和方形边界的三个角度数组，单位 deg；离线执行时按批次计算，点间没有真实播放时间。
- neck/kinematics -> analysis/calibration : calibration_scan_results | 范围计算结果 | 数值 | 实时 | 每点对应的电机角度 deg、模型余量 deg 和可达标记；随扫描批次返回，工具结合机械范围形成最终判断。
- analysis/calibration -> external/client : calibration_result_report | 范围判断结果 | 文本 | 实时 | 零位、边界、角度范围和一致性结果的终端文字，角度单位 deg；每次离线工具运行输出一次过程与结果，不访问硬件。
- neck/logs -> analysis/ripple_builder : compensation_feedback_files | 建表反馈数据 | 曲线 | 文件 | 一份或多份反馈 CSV，使用时间 s、位置 deg、指令速度 rad/s 与电流 A；每次建表批量读取，原记录频率通常为 1000 Hz。
- neck/config -> analysis/ripple_builder : ripple_raw_table_file | 原始补偿读取 | 数值 | 文件 | 已保存的 JSON 原始表，每电机每方向 500 个力矩值 N·m；选择原始表作为输入时读取一次，不同时读取反馈 CSV。
- analysis/ripple_builder -> neck/config : ripple_tables_written | 补偿表保存 | 数值 | 文件 | JSON 力矩数组，每电机正反向各 500 项、单位 N·m、周期 10 deg；每次建表写入一次，筛选模式保留第 7、14、28 次周期成分并另存原始表。
- analysis/ripple_builder -> external/client : ripple_build_report | 补偿建表结果 | 文本 | 实时 | 终端统计文字，包含峰峰值 N·m、无量纲相关系数及样本数量；按电机与方向输出，每次建表运行产生一批。
- analysis/structure_files -> analysis/structure_view : structure_markdown_file | 模块说明读取 | 文本 | 文件 | Markdown 模块和数据流描述，无物理单位；每次内容请求重新读取，网页正常每 3 s 请求一次。
- analysis/structure_files -> analysis/structure_view : structure_html_file | 网页内容读取 | 文本 | 文件 | HTML、样式和浏览脚本的文本内容，无物理单位；每次网页资源请求读取一次。
- external/client -> analysis/structure_view : structure_http_request | 项目浏览请求 | 文本 | 实时 | 网页资源或模块说明的 HTTP GET、HEAD 请求，无物理单位；默认访问本地 8765 端口，模块说明由网页每 3 s 请求一次。
- analysis/structure_view -> external/client : structure_html_response | 项目浏览网页 | 文本 | 实时 | 包含模块展开、连接详情和类型筛选交互的 HTML 响应，无物理单位；打开或刷新网页时返回一次。
- analysis/structure_view -> external/client : structure_markdown_response | 模块说明响应 | 文本 | 实时 | 转交的 Markdown 文字，无物理单位；随请求返回，网页每 3 s 取得内容，变化时更新模块和数据流图。
