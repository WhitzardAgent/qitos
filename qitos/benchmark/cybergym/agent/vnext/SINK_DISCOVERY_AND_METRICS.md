# Sink Discovery、专业知识库与指标预测

本文定义 vNext 的结果目标、sink 指标、漏洞知识规则、TSA 移植优先级和收益预测。它是 Task 01–05 的共同决策依据，不是额外子任务。

## 1. North-star 与因果链

唯一最终目标：提高 agent 构造的 PoC 使目标程序发生预期 crash 的比例。

```text
Crash rate
  <- PoC 能通过 carrier/dispatch/reachability gates
  <- agent 选择了正确 crash path 或足够接近的 causal path
  <- sink candidate set 覆盖真实 crash site/causal site
  <- call graph、漏洞语义、描述线索和 harness reachability 足够完整
```

因此 vNext 的核心中间目标不是“静态分析输出更多”，而是：

1. 真实 crash site 出现在 sink candidates Top-K。
2. 即使 exact top frame 暂时未命中，candidate path 也覆盖 top crash frames 或 causal site，使模型能构造有效输入。
3. candidates 足够多样但仍有预算，模型不会被五个同类 wrapper 淹没。
4. sink 结论进入 context 后能直接驱动 READ、gate 提取和 PoC byte layout。

## 2. 数据基线

### 当前 v12 轨迹

- 83 traces，61 completed，46 crash，completed crash rate = **75.4%**。
- 82 条有 GT 的轨迹中，现有 sink/chain coverage = **34/82 = 41.5%**。
- 轨迹中每次平均只记录约 **1.2 个 sink candidates**；绝大多数只有一个候选，尚未真正形成 Top-K recall 机制。
- 在当前可完成样本中，sink covered 时 crash 约 **24/29 = 82.8%**，sink missed 时约 **22/32 = 68.8%**。这是相关性，不是因果估计：简单任务同时更容易定位 sink 和产生 crash，但约 14pp 的差异证明 sink coverage 是值得优化的中间指标。
- 14 条主要损失均为“已经提交但未触发直到超时”，优先问题是 path/sink/condition 不准确，不是 agent 不生成 PoC。

样本仍小：Wilson 95% 区间约为 crash rate 63.3–84.5%、sink coverage 31.4–52.3%、sink-covered crash 65.5–92.4%、sink-missed crash 51.4–82.0%，条件区间明显重叠。因此 14pp 只能用于方向和机械外推，不能宣称为 sink coverage 的因果提升。正式 A/B 必须使用相同 task manifest 和 project-level bootstrap interval。

### 全量任务与 error stack

数据来源：`cybergym_full_tasks/tasks.json` 的 1507 个任务，以及从 Hugging Face 数据集仅按 `data/**/error.txt` 白名单下载的 1507 份 crash logs。error.txt 只用于离线统计与评测，Level-1 runtime 绝不读取它。

- 语言：C++ 1276（84.7%）、C 228（15.1%），其他 3。
- 主要 sanitizer 类别（按 error 文本归一化，近似计数）：
  - heap/stack/global buffer overflow：约 734+，接近一半任务。
  - use-of-uninitialized-value：287（约 19%）。
  - SEGV：144（约 9.6%）。
  - heap UAF、use-after-poison、stack-use-after-*：150+。
  - double/invalid free：30+。
  - 另有 negative-size、container overflow、memcpy overlap、UBSan/assertion 等。
- ARVO 1368 条中可解析 1361 条 primary crash stack：
  - top crash frame 到 `LLVMFuzzerTestOneInput` 的 rank 中位数 7，P75=11，P90=21，最大值远高于 24。
  - project frames 数量的中位数同样为 7，P90=21。
  - 固定 `max_depth=8` 只适合快路径，不足以作为完整 candidate recall 上限。
- 描述与 stack 的关系：
  - description 直接包含 GT top sink/leaf 的约 20.7%。
  - description 提到 primary project stack 任一函数的约 30.4%。
  - 一旦命中 stack，约 93.7% 位于前 3 个 project frames，约 98% 位于前 5 个。
  - 结论：description anchor 是高价值局部种子，但经常指向 top sink 的 caller，而不是 exact crash instruction。
- GT top site 的粗粒度形态：约 70% plain project function、16% qualified method、9% low-level/semantic primitive、3% template、1% operator，另有极少数 file/line site。

## 3. 必须统一的 Sink 定义

一个候选必须带 `candidate_role`，否则“命中 sink”与“足以做 PoC”会混淆：

- `crash_site`：sanitizer primary stack 的第一个相关 project frame；exact sink recall 的 GT。
- `causal_site`：导致崩溃状态的写入/释放/未初始化 origin，例如 UAF 的 realloc/free、MSan 的 incomplete initialization。
- `path_anchor`：description 指向或模型理解到的 parser/dispatcher/caller；可用于导航，但不能冒充 crash site。
- `dangerous_primitive`：memcpy、typed endian read、operator[]、decoder inner loop 等直接危险操作；可能是 external/inline/template symbol。

候选状态还要区分：

- `source_backed`：有 call edge/risk signal/source span。
- `reachable_verified`：从 selected harness 存在 resolved path。
- `reachable_partial`：路径含 unresolved indirect/virtual edge。
- `description_prior`：只来自自然语言，尚未验证。

`record_sink_candidate` 可以选择 path anchor 作为当前工作点，但自动 candidate set 必须尽量同时包含该 anchor 下游的 crash-site/primitive candidates。

## 4. 指标体系

### 最终指标

- `completed_crash_rate = crash / completed`
- 同时报告 `crash / all_started`，防止通过延长运行或留下 running 人为提高 completed rate。
- `submitted_no_trigger_timeout_rate`
- 首次 crash 的 median/P75 steps 与 wall time。

### Sink 精度与覆盖

必须同时报告：

1. `ExactSinkRecall@1/@3/@5`：GT crash site 是否在有序 crash_site candidates 中。
2. `CrashPathRecall@K`：候选是否覆盖 primary stack top 3/top 5 任一 project frame。
3. `CausalCoverage@K`：对 UAF/uninitialized/double-free，candidate 是否覆盖 use+free/origin 事件中的至少一个；另报 event-pair coverage。
4. `GraphDistanceToGT`：Top-1 candidate 到 GT 的最短 resolved call-edge 距离；0 为 exact，1–2 为 near-sink。
5. `CandidateFamilyDiversity@K`：Top-K 是否来自不同 endpoint/path/file/role；禁止五个同一 wrapper。
6. `ReachabilityPrecision@K`：候选中有 selected harness path 的比例；partial 单列，不能当 false。
7. `ScoreCalibration`：按 score bucket 统计 exact/path hit rate，避免 opaque score 看似精确。

### Context 到行为的转化指标

- Top-5 中有 GT，但模型 active sink 未选中：ranking/prompt failure。
- Active path 有 GT，但 PoC no-trigger：gate/mapping failure。
- Required Conditions 有 confirmed mapping，但 PoC 未修改对应 byte：prompt-to-action failure。
- candidate set 无 GT：static discovery failure。

这四类必须在评测脚本中分开，才能知道下一版该修分析、排序还是 prompt。

### 无泄漏评测

- `error.txt` 只作为 offline label/parser regression fixture，不进入 Level-1 state、prompt 或 runtime filesystem。
- 参数/权重在固定 development split 上调；最终预测在 held-out projects 上报告，优先 project-level split，避免同一项目相似函数泄漏。
- exact symbol normalize 必须支持 C++ template、qualifier、operator、lambda，但不能用过宽 substring 产生虚假命中。

## 5. Candidate 生成策略

候选不能只来自一个 scorer。Task 03 应合并 5 个召回通道，再统一排序/去重：

1. **Description-local expansion**：verified description refs 本身作为 path_anchor，并向 callees/downstream risk signals 扩展 3–5 hops。
2. **Entry-forward expansion**：从 selected harness/first hops 前向遍历；深度 8 是 fast tier，若候选不足或只有 wrapper，扩到 24。
3. **Risk-backward expansion**：从 crash-type-compatible risk endpoints/semantic primitives 反向找 callers，与 forward frontier meet-in-the-middle。
4. **Structural hazard detection**：不依赖函数名，识别 input-controlled pointer/index/length/lifecycle/uninitialized patterns。
5. **C++ dispatch expansion**：qualified method、template/operator、function pointer table、virtual override 的可能目标；保留多目标和 confidence，不随便取第一个。

候选池建议先保留 30–50 个轻量 endpoint，再排序、多样化为模型可见 Top-5。不要在图构建早期只截断 5 个，否则 recall 无法恢复。

### 自适应搜索预算

- Tier 1：resolved edges，depth≤8，快速产生 early candidates。
- Tier 2：若 Top-5 无 direct risk、description anchor 未连接或 unresolved 比例高，则 bidirectional/beam search 到 depth≤24。
- Tier 3：只对 C++ virtual/function-pointer gap、UAF event pair 或 repeated no-trigger 做 targeted expansion，不全仓无限扩展。
- cycle/SCC 内按 node+callsite 去重；同一 endpoint 最多保留 2 条路径。

## 6. 专业漏洞知识规则

规则必须描述“结构证据”，而不是把函数名关键词等同于漏洞。每条 rule 输出 endpoint role、critical args、required event sequence、confidence reasons 和 false-positive guards。

### 6.1 Buffer overflow / OOB read-write / container overflow

候选 sink 不限于 memcpy：

- 显式 memory/string APIs：`memcpy/memmove/memset/strcpy/strncpy/strcat/sprintf/read/recv/fread`。
- 直接数组/指针访问：`base[index]`、`*(ptr + offset)`、iterator dereference、span/array accessor。
- typed reads/writes：`read16/32/64`、`get_le/be*`、`MEM_read*`、`operator T()`、`operator[]`、bitreader/byte reader。
- codec/format loops：output pointer increment、row/stride conversion、run-length copy、entropy/bit decode、pixel/sample conversion。
- container growth/copy：append/insert/push/resize 后 stale pointer，length/capacity mismatch。

结构加分：critical index/length 被 input control；guard 使用错误单位；`count * width`/`offset + size` 溢出；signed→unsigned；allocation size 与 access size 不同源；loop bound 与 destination capacity 无关。

降权：constant-size access 且有支配性 bounds check；仅函数名含 read/copy；不可达；risk 参数不受 input 控制。

关键参数：pointer/base、index/offset、length/count、element width、allocation/capacity、stride。

### 6.2 Use-after-free / use-after-poison / stack lifetime

必须建模事件对，不可把 `free` 单独当 crash site：

- invalidation：free/delete/realloc、unref/release/destroy、container erase/clear/resize、vector growth、scope exit/destructor、arena reset。
- later use：deref/member call/copy/compare/hash/callback invocation/iterator use。
- ownership patterns：borrowed pointer 保存跨 callback；entity/object互相引用；error path 清理后 fallthrough；alias 未清零；move 后继续使用。

输出至少两个候选：`causal_site=invalidation` 和 `crash_site=use`，并给 alias/evidence gap。若只找到一端，标 partial，不给完整 UAF score。

### 6.3 Double-free / invalid-free / bad-free

- 同一 alias 在 normal+error cleanup 两条路径释放。
- realloc 失败/成功语义处理错误。
- ownership transfer 后 caller/callee 双方释放。
- interior/shifted pointer、stack/global pointer 传给 deallocator。
- allocator family mismatch：new/free、malloc/delete、library custom allocator mismatch。
- refcount/unref 下溢或重复 decrement。

关键参数是 deallocated pointer、alias set、allocator family、path condition；sink 常是 project cleanup wrapper，而不只是 libc free。

### 6.4 Use-of-uninitialized-value

高频且当前 sink coverage 最弱，必须独立处理：

- local/field/out-param 在所有到达路径上是否定义。
- producer 返回 success 但只部分写 out-param。
- struct padding/union inactive member 被 copy/compare/hash/serialize。
- branch/loop bound/index/length 使用未初始化值。
- realloc/growth 后新增区域未初始化。
- decoder 在 short input/error path 上提前退出，consumer 仍读取。

候选角色：`causal_site=missing initialization/partial producer`，`crash_site=first branch/read/copy consuming value`。MSan origin stack 的规律用于离线验证 event-pair recall，但 runtime 仍靠 def-use/source evidence。

### 6.5 Negative size / integer overflow / truncation

- signed negative 转 `size_t`/unsigned。
- `count * element_size`、`offset + length`、alignment round-up overflow。
- subtraction underflow：`remaining = end - ptr`、`size - header`。
- narrowing/truncation：64→32、int→short、enum/bitfield。
- sentinel `-1` 被当长度；API 返回值未检查后作为 size。

真正 endpoint 是算术结果进入 allocation、copy、loop bound 或 pointer arithmetic 的位置；算术表达式和 consumer 都应成为 paired candidates。

### 6.6 Null/SEGV/unknown address

- nullable allocator/lookup/cast/parse result 未检查后 deref/member call。
- invalid discriminator/tag 导致 union/member/function pointer 错误。
- corrupted pointer 来自 OOB/integer/lifetime，应优先根因 pattern，不能所有 SEGV 都归 null。
- wild address free/call 通常提示 pointer corruption、bad cast 或 incorrect function pointer。

SEGV 的 scorer 应返回 competing hypotheses（null, corrupted index, UAF, bad cast/function pointer），由 source evidence消歧。

### 6.7 Type confusion / bad cast / incorrect function pointer

- C-style/reinterpret/static downcast 与 runtime tag 不一致。
- union active member、variant discriminator、object kind/type field 未验证。
- callback/function pointer 从表/输入 selector 取出后签名不匹配。
- C++ virtual dispatch receiver dynamic type 不符合静态假设。

候选包括 cast/tag check 的 causal site 与首次 field/method/callback use 的 crash site。

### 6.8 Memcpy overlap

- source/destination 来自同一 base object，区间可能交叠；目标 API 是 memcpy/strcpy 而非 memmove。
- critical mapping 是两段 `[base+off, base+off+len)`；必须输出 overlap constraint，不能只 trace length。

### 6.9 Recursion/resource/assertion/UBSan

- uncontrolled recursion、no-progress loops、allocation size/resource count、assert reachable from input。
- shift width、division by zero、misaligned access、invalid enum、non-flexible array UBSan。
- 这类不应被 memory-copy registry 淹没；若 crash type unknown，保留至少一个 non-memory candidate family。

## 7. TSA 选择性移植计划

参考 `../tree-sitter-analyzer/tree_sitter_analyzer`，只能按 recall gap 选择性移植，并用 CyberGym C/C++ fixture 验证；不要整文件覆盖当前已定制版本。

### P0：直接影响 sink recall

1. **C++ symbol/call extraction hardening**
   - 对比 `function_extraction.py`、`call_graph.py` 的 template/operator/destructor/qualified-method/file-aware fixes。
   - 目标：GT 中 qualified/template/operator 约 20% 的 symbol 能被稳定索引和 normalize。
   - 只移植当前实现缺失的 AST cases/bug fixes；当前 `analysis/indexer.py` 已有 function-pointer assignment/table 和 receiver_type 逻辑，必须保留。

2. **File-aware bidirectional path fixes**
   - 借鉴 TSA `call_path.py` 的 file-aware signature、name-only wildcard meet、resolved callee file 语义。
   - 在现有 `AnalysisService.edges` 上实现，不引入第二套 SQL graph。
   - 将 fixed depth 8 改成 8→24 adaptive search。

3. **Class hierarchy + virtual override expansion**
   - 移植 `class_hierarchy.py` 的 inheritance extraction/缓存结构。
   - 仅有 hierarchy 不够；要结合 `CallSite.receiver_type` 和 method name 生成 base/override target edges，标记 `virtual_override` confidence。
   - 同名 class/file collision 必须保留 TSA 的 per-parent disambiguation 思路。

### P1：减少 unresolved/错误绑定

4. **Cross-file resolver 的 C/C++ 适配**
   - TSA `cross_file_resolver.py` 主要覆盖 Python/JS/Java/Go import；不能原样宣称解决 C++。
   - 复用它的 module/function index 与 confidence contract，新增 include path、header declaration→implementation、static/internal linkage、namespace/overload arity 规则。
   - 验收看 unresolved-call reduction 与错误 edge precision，而不是“文件已移植”。

5. **XRef query contract**
   - `xref.py` 的统一 definitions/callers/callees/file dependents 返回适合 targeted gap repair。
   - 当前 service 已有同类内存结构；优先移植统一返回契约和 cache query 思路，不重复建索引。

### P2：弱 prior/fallback

6. **SemanticSymbolSearch**
   - TSA 实现是 deterministic token-vector/BM25 fallback，不是真正 embedding semantic search。
   - 用于 description exact/casefold 均无结果时的低权重候选扩展；不能单独确认 sink。
   - 要测 false-positive rate，尤其 common verbs、test symbols 和同名 APIs。

## 8. 任务优先级调整

- Task 01：除结构化描述外，建立 vulnerability semantics classification 和 description-local expansion；目标是高质量 seeds，不是直接 sink verdict。
- Task 02：harness first hops 与 consumption gates 为 reachability 提供可靠入口，避免高风险但不可达候选。
- Task 03：vNext 的核心收益任务。实现多通道召回、专业知识 registry、TSA P0 移植、自适应路径、Top-K diversity 和 sink metrics。
- Task 04：把正确 path/sink 转成能触发的 byte/sequence conditions，提高“sink hit → crash”的转化率。
- Task 05：离线 error-stack evaluator、held-out A/B、context/prompt 行为归因和灰度发布。

## 9. 预测

以下为规划预测，不是保证值；以 project-held-out A/B 为准。

### Sink 指标

当前 coverage 41.5%，且平均仅 1.2 candidates。基于描述 stack-locality、Top-K 扩容、P90 path depth=21 和 C++ dispatch 修复：

- 保守：ExactSinkRecall@5 **60–68%**。
- 目标：ExactSinkRecall@5 **68–76%**。
- Stretch：**78%+**，需要 virtual/function-pointer/template edges 在复杂项目上稳定。
- CrashPathRecall@5 预计高于 exact recall，目标 **80–88%**。
- ExactSinkRecall@1 目标 **52–60%**；不应为了 Top-1 precision 过早剪掉真实 sink。

### Crash rate

按当前诊断样本，sink-covered 与 missed completed crash rate 相差约 14pp，但存在难度混杂。若 coverage 提升到 65–75%，仅按当前条件率机械外推，completed crash rate 约 **78–80%**。再叠加 Task 02/04 对 carrier gates 和 byte mapping 的转化增益：

- 保守预期：**78–81%** completed crash rate。
- 合理目标：**81–84%**。
- Stretch：**85–87%**，前提是 no-trigger timeouts 显著下降且没有因 context 膨胀/过度分析减少提交次数。

不建议承诺 90%：剩余任务包含 deep stateful paths、indirect dispatch、复杂 format carriers 和 sanitizer-specific semantics，sink 命中也不等于可在预算内构造 discriminating input。

### 分任务预期贡献

- Task 01：Exact Recall@5 +5–10pp，主要来自描述提到 caller/模块但不提 top sink 的任务。
- Task 02：Exact recall 小幅 +2–4pp，但 crash rate +1–2pp，来自正确 harness/dispatch/carrier。
- Task 03：Exact Recall@5 +15–25pp，是主要 sink 增益来源。
- Task 04：sink recall 基本不变；sink-covered → crash conversion +3–6pp。
- Task 05：本身不直接增益，但应防止 context/prompt 回归吞掉前四项收益，并给出可信 A/B。

贡献不可简单相加；任务间高度重叠，最终以端到端 A/B 为准。
