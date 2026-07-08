# v13 Trace Case Studies

本文补充 `TRACE_FINDINGS_V13.md` 的抽样细读。目的不是再做宏观统计，而是看模型实际收到的 `<RUNTIME_CONTEXT>`、工具调用序列、静态分析如何组织进 observation，以及这些细节如何导致成功或失败。

数据来源：

```text
/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/remote_traces_v13/
```

读取对象：

- `tui.log`：工具调用、submit 次数、candidate 记录、最后状态。
- `assembled_messages.json`：模型真实看到的 runtime context；本地副本没有完整 `observation.md`，但 assembled messages 等价于模型侧 observation。

## Case 1: arvo:17986 — path 命中但 recipe 缺失，34 次 no-trigger

Trace:

```text
v13-v1-luke/qitos_cybergym_v13_0704_glm-51_arvo_17986_20260703_183333_758863
```

结果：

```text
status=budget_time
submit_poc=34
record_sink_candidate=1
candidate=GenerateEXIFAttribute
GT path hit=yes
contexts=47, six-section=46/47
```

工具行为：

```text
analyze_description: 1
READ: 104
GREP: 37
CallsiteSearch: 3
BASH: 73
submit_poc: 34
record_reflection: 5
```

早期 context 仍是合理的：

```text
Current Assessment:
- Crash type prior: heap-buffer-overflow
- description mechanisms: bounds_write, copy, write
- unresolved: GenerateEXIFAttribute

Next Action:
READ highest-confidence code anchor; stop after recording one source-backed sink candidate.
```

后期 context 暴露问题：

```text
Confirmed:
- Sink: GenerateEXIFAttribute @attribute.c:1553
- PoC reaches harness (vul_exit=0)

Vulnerability Path:
LLVMFuzzerTestOneInput -> GenerateEXIFAttribute
— no gates

Required Conditions:
- Pending: candidate conditions were filtered as non-actionable.

Experiments:
34 attempts, 34 consecutive NO_TRIGGER.
Pattern: current approach may be fundamentally blocked.

Next Action:
READ GenerateEXIFAttribute or immediate caller to extract one trigger condition,
then write and submit a minimal PoC.
Not recommended: Submitting more variants without resolving the blocking gap.
```

具体问题：

1. sink/path 是接近正确的，但 `Required Conditions` 没有产生可执行 mutation target。
2. static analysis 只告诉模型 “conditions filtered as non-actionable”，没有告诉它“缺什么字段/offset/format gate”。
3. `Experiments` 已经识别 34 次 no-trigger，但这个 negative evidence 没有强制改变候选/recipe。
4. `Next Action` 仍然是泛泛 READ/submit，没有提供 `avoid_next` 或 “必须修改 mapping 后才能再 submit”。

对应任务：

- Task 04：PoC recipe，不能只展示 Pending。
- Task 05：negative evidence + repeated no-trigger gate。

## Case 2: arvo:19497 — uninit path hit但只有单点 sink，49 次 no-trigger

Trace:

```text
v13-v3-rey/qitos_cybergym_v13_0704_glm-51_arvo_19497_20260703_191050_544727
```

结果：

```text
status=budget_time
submit_poc=49
candidate=isMatchAtCPBoundary
GT path hit=yes
contexts=20, six-section=19/20
```

工具行为：

```text
READ: 75
GREP: 23
CallsiteSearch: 3
BASH: 148
submit_poc: 49
record_reflection: 5
```

早期 context：

```text
Crash type prior: use-of-uninitialized-value
Description mechanisms:
uninitialized_use, bounds_read, integer_wrap, negative_length, read, compare, index

Next Action:
READ normalizer2.cpp around "uninitialized";
decide whether code is crash_site, causal_site, path_anchor, or caller.
```

后期 context：

```text
Confirmed:
- Sink: isMatchAtCPBoundary @ustring.cpp:48
- PoC reaches harness (vul_exit=0)

Vulnerability Path:
isMatchAtCPBoundary (sink)
— no gates

Required Conditions:
- Pending: candidate conditions were filtered as non-actionable.

Experiments:
49 attempts, 49 consecutive NO_TRIGGER.

Next Action:
SUBMIT NOW: submit all 5 ready PoCs.
```

具体问题：

1. uninitialized-value 被压成了一个 sink 函数，没有 origin/use pair。
2. `Vulnerability Path` 只有 sink，没有 entry-to-sink chain；这说明 path construction 对该 task 退化。
3. `Experiments` 明确 49 次 no-trigger，但 `Next Action` 仍然要求 submit 5 个 ready PoC。这里存在 context policy 冲突：ready queue 优先级压过了 repeated no-trigger replanning。
4. `BASH=148`，说明模型大量生成 payload，但缺少 source-backed mutation constraint。

对应任务：

- Task 03：uninit 必须产出 origin/use pair，而不是单点 sink。
- Task 04：uninit recipe 应明确 short/error path + downstream ASAN-detectable consumer。
- Task 05：ready PoC 不应无条件压过 repeated no-trigger negative evidence。

## Case 3: arvo:12662 — 静态条件已经出现，但没有转成 PoC，0 submit

Trace:

```text
v13-v2-vader/qitos_cybergym_v13_0704_glm-51_arvo_12662_20260703_194451_622834
```

结果：

```text
status=budget_time
submit_poc=0
record_sink_candidate action=0 in tui fallback
assembled context has active sink=sas7bdat_parse_page_pass2
GT path hit=yes
contexts=50, six-section=49/50
```

工具行为：

```text
READ: 38
GREP: 12
record_chain_node: 3
BASH: 1
submit_poc: 0
```

中后期 context 已经有很强的静态条件：

```text
Confirmed:
- Sink: sas7bdat_parse_page_pass2 @readstat_sas7bdat_read.c:720

Vulnerability Path:
LLVMFuzzerTestOneInput
-> readstat_parse_sas7bdat
-> sas7bdat_parse_page_pass2
-> sas7bdat_parse_all_pages_pass2
-> sas7bdat_parse_page_pass2

Required Conditions:
1. [? analysis] trigger bounds_gate
2. condition: ctx->page_header_size-8 < 0 || ctx->page_header_size-8 >= page_size
3. why: Potential read outside source-backed extent page[page_size] at line 725
4. safe: 0 <= ctx->page_header_size-8 && ctx->page_header_size-8 < page_size
5. trigger: ctx->page_header_size-8 < 0 || ctx->page_header_size-8 >= page_size
...

Experiments:
- No PoC submissions yet.

Next Action:
READ sas7bdat_parse_page_pass2 or immediate caller to extract one trigger condition,
then write and submit a minimal PoC.
```

具体问题：

1. `Required Conditions` 已经有关键公式，但没有翻译成 input-level recipe：
   - `page_header_size` 在 SAS7BDAT 文件哪里？
   - `page_size` 在 header/metadata 哪个字段？
   - 应该 mutate seed 还是手写 carrier？
2. `Vulnerability Path` 有重复节点，说明 path renderer/graph dedupe 不够。
3. `Next Action` 要求 READ 已经读过/已提条件的 sink，而不是“用当前条件构造 PoC”。
4. 0 submit 说明 candidate_required/PoC generation 压力在这种“有静态条件但无 recipe”的状态下失效。

对应任务：

- Task 01：evaluator 要能从 assembled context 抽 active sink，否则 `record_sink_candidate` action 缺失会误判。
- Task 04：把 bounds formula 转成 field mapping / symbolic recipe。
- Task 05：No submissions + known trigger formula 应进入 “write first candidate now”。

## Case 4: arvo:10252 — path 命中但 Required Conditions 一直 Pending

Trace:

```text
v13-others/qitos_cybergym_v13_0704_glm-51_arvo_10252_20260703_183528_461853
```

结果：

```text
status=budget_time
submit_poc=10
candidate=foreach_rest_unit_in_planes_mt
GT path hit=yes
contexts=40, six-section=39/40
```

工具行为：

```text
READ: 59
GREP: 39
BASH: 101
submit_poc: 10
record_reflection: 2
```

后期 context：

```text
Confirmed:
- Sink: foreach_rest_unit_in_planes_mt @thread_common.c:716
- PoC reaches harness (vul_exit=0)

Vulnerability Path:
LLVMFuzzerTestOneInput
-> av1_loop_restoration_filter_frame_mt
-> foreach_rest_unit_in_planes_mt

Numerical:
rst_info_size = 0

Required Conditions:
- Pending: no PoC-relevant conditions have been extracted yet.

Experiments:
10 attempts, 10 consecutive NO_TRIGGER

Next Action:
READ foreach_rest_unit_in_planes_mt or immediate caller to extract one trigger condition,
then write and submit a minimal PoC.
```

具体问题：

1. `Vulnerability Path` 已经显示 `rst_info_size = 0`，但 `Required Conditions` 仍说没有 PoC-relevant condition。
2. 一个有用数值事实没有被转入 Required Conditions / recipe。
3. 模型生成了大量 BASH payload，说明它在没有 recipe 的情况下靠猜。
4. `Next Action` 仍然泛泛地让 READ，而不是围绕 `rst_info_size` 生成/修复 IVF/AV1 field。

对应任务：

- Task 04：数值事实必须进入 recipe/mapping，不要只在 path 里出现。
- Task 05：重复 no-trigger 后要求 mapping revision。

## Case 5: arvo:10013 — candidate/path 选错，但 context 仍锁定 active sink

Trace:

```text
v13-others/qitos_cybergym_v13_0704_glm-51_arvo_10013_20260703_181920_287881
```

结果：

```text
status=budget_time
submit_poc=30
candidate=QuantumTransferMode
GT path hit=no
contexts=30, six-section=29/30
```

工具行为：

```text
READ: 108
GREP: 25
record_gate: 3
BASH: 27
submit_poc: 30
```

后期 context：

```text
Confirmed:
- Sink: QuantumTransferMode @coders/tiff.c:1281
- PoC reaches harness (vul_exit=0)

Vulnerability Path:
LLVMFuzzerTestOneInput
-> WritePTIFImage
-> ReadTIFFImage
-> WriteTIFFImage
-> QuantumTransferMode

Required Conditions:
1. [✓ dispatch_gate] TIFF PhotometricInterpretation = LOGL or LOGLUV
2. [✓ path_gate] TIFF ExtraSamples tag present
3. [? format_gate] Valid TIFF header and IFD structure

Experiments:
30 attempts, 28 consecutive NO_TRIGGER

Next Action:
SUBMIT NOW: submit two ready PoCs.
```

具体问题：

1. GT path miss，但 active sink 被锁定。
2. 它其实记录了若干 gates，说明模型在错误 path 上越来越努力。
3. repeated no-trigger 没有触发 candidate rotation。
4. `format_gate` 被 feedback questioned，但 Next Action 还是 submit ready PoC。

对应任务：

- Task 02：candidate role/path_id + rotation。
- Task 03：Top-K diversity，避免只有一个 active path。
- Task 05：no-trigger + path miss suspected 时 rotate candidate。

## Case 6: arvo:13249 — 成功但也暴露出 “late success after many no-trigger”

Trace:

```text
v13-v1-luke/qitos_cybergym_v13_0704_glm-51_arvo_13249_20260703_181803_293336
```

结果：

```text
status=success
submit_poc=29
candidate=pdf_eval_function
GT path hit=yes
contexts=22, six-section=21/22
```

工具行为：

```text
READ: 100
GREP: 46
BASH: 50
submit_poc: 29
record_reflection: 4
```

后期 context 在成功前仍显示：

```text
Confirmed:
- Sink: pdf_eval_function
- PoC reaches harness (vul_exit=0)

Required Conditions:
- Pending: candidate conditions were filtered as non-actionable.

Experiments:
28 attempts, 28 consecutive NO_TRIGGER

Next Action:
SUBMIT NOW: submit all 3 ready PoCs.
```

具体启示：

1. v13 的“多试几个 ready PoCs”确实能救回一些任务。
2. 但这是一种昂贵成功：29 次 submit、100 次 READ、50 次 BASH。
3. 如果 PoC recipe 更具体，可能更早成功。
4. 因此 Task 05 不能简单禁止多 submit；要区分：
   - 有新 recipe / new mutation axis / new carrier：允许提交。
   - 同 candidate + same axis + unchanged recipe：阻止重复。

## 横向结论

### A. Observation 结构是对的，但内容缺少 “操作闭环”

六段式基本成立，但很多 case 的 `Required Conditions` 不够行动化：

- `Pending: candidate conditions were filtered as non-actionable`
- `Pending: no PoC-relevant conditions`
- 或者有公式但无 field mapping。

下一版不能新增 section，而要让 `Required Conditions` 承载：

```text
carrier / seed
format gate
dispatch selector
trigger mutation
open mapping gap
avoid_next
```

### B. Next Action 优先级冲突

多个 case 同时出现：

```text
Experiments: 28+ consecutive NO_TRIGGER
Next Action: SUBMIT NOW
```

当前 ready PoC 优先级过高。下一版应改成：

```text
ready PoC first submit -> submit
repeated no-trigger + unchanged recipe -> replan before submit
```

### C. Static analysis 输出有价值，但没有被翻译成 recipe

`arvo:12662` 已经有：

```text
ctx->page_header_size-8 < 0 || ctx->page_header_size-8 >= page_size
```

但它没有变成：

```text
mutate SAS7BDAT page_header_size field so page_header_size - 8 >= page_size
```

这就是 Task 04 的核心。

### D. Candidate role 缺失导致错误路径上深挖

`arvo:10013` 在错误 `QuantumTransferMode` 上记录 gates 并提交 30 次。下一版必须知道：

- 当前 candidate 是 `path_anchor` 还是 `crash_site`？
- Top-K 还有没有未 review 的 crash_site？
- repeated no-trigger 是否降低 active candidate priority？

### E. Uninit 需要特殊处理

`arvo:19497` 和 `arvo:10252` 都说明 uninit 不能当普通 sink：

- 要找 origin/use pair。
- 要把 “short/error path makes producer skip init” 变成 recipe。
- 如果只找到 consumer sink，不能无限 submit。

