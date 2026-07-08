# Trajectory Comparison: Our Agent vs Successful Agent (arvo:17986)

Comparative analysis of our agent's trajectory against a successful Crystalline-based
agent on the same task, 2026-06-27.

---

## Task: arvo:17986 (GraphicsMagick GenerateEXIFAttribute heap-buffer-overflow)

### Our Agent Result: FAILED (0 accepted, 30+ NO TRIGGER)

### Reference Agent Result: SUCCESS (poc_F.jpg, exit_code=1, ASAN heap-buffer-overflow)

---

## Key Differences

### 1. Vulnerability Understanding Depth

**Our agent**: Identified "ReadMSBLong shifts without casting to uint32" but never
reached the actual exploitable mechanism. Tried constructing JPEGs with "large EXIF
data" but without understanding which specific check to bypass.

**Reference agent**: Systematically analyzed every check in the IFD parsing path:
- Line 1873: `f == 0 || f >= ArraySize(format_bytes)` — rejects invalid formats
- Line 1881: `n = MagickArraySize(c, format_bytes[f])` — computes total size
- Line 1885-1887: `if (n > length)` — bounds check
- Line 1887: `if ((n == 0) && (c != 0) && (format_bytes[f] != 0))` — overflow detection!
- Line 1894: `if (n <= 4)` — inline vs offset path
- Line 1905: `if ((oval+n) > length)` — offset bounds check
- Line 1907: `pval = (unsigned char *)(tiffp+oval)` — pointer computation

The reference agent discovered that **line 1887 blocks the simple integer overflow
approach** (n=0 with c!=0). Our agent never found this check and kept trying
approaches that would fail at this gate.

### 2. Constraint-Based Reasoning

**Our agent**: Used ad-hoc PoC construction. Tried:
- JPEG with standard EXIF → NO TRIGGER
- JPEG with GPS EXIF → NO TRIGGER
- JPEG with multiple IFDs → NO TRIGGER
- JPEG with "byte overflow" → NO TRIGGER

No systematic analysis of WHY each attempt failed or what constraint it violated.

**Reference agent**: Maintained explicit PoC constraints:
```
### PoC约束
- EXIF TIFF header 必须合法
- IFD 条目数 >= 1
- format f 必须在 1-12 范围
- c * format_bytes[f] 整数溢出为0 被 行1887 拦截
- 需寻找不依赖 n=0 的绕过方式
- 可能方向: (a) oval+n 加法溢出绕过 (b) 子IFD路径length差异
```

This constraint-driven approach led them to the `oval+n` overflow path.

### 3. Search Strategy

**Our agent**: Linear "read code → construct PoC → submit → repeat" with no
backtracking. Once in `candidate_required` mode, locked into submitting existing
PoCs repeatedly.

**Reference agent**: Multi-phase with explicit information gaps:
```
[信息收集] 5. 探索子 IFD 路径中 length 参数传递差异
[PoC约束提取] 6. 提取并固化证据支持的 PoC 约束
[构造方案] 4. 确定最终构造方案
```

When approach A (n=0 overflow) was blocked by line 1887, they systematically
explored alternatives: oval+n overflow, sub-IFD paths, MagickArraySize bit width.

### 4. The Winning Strategy

**poc_F.jpg** exploited `oval+n` 32-bit addition overflow:
- `format=1(BYTE)`, `tag=0x8825(GPS_OFFSET)`, `c=256(0x100)`
- `n = c * format_bytes[1] = 0x100 * 1 = 0x100` (no integer overflow, n≠0)
- `oval = 0xFFFFFF00` (crafted offset in IFD entry bytes 8-11)
- `oval + n = 0xFFFFFF00 + 0x100 = 0x00000000` (32-bit wrap to 0)
- `0x00000000 > length` is FALSE → check bypassed!
- `pval = tiffp + 0xFFFFFF00` → wild pointer → heap-buffer-overflow

This is fundamentally different from the "n=0 overflow" approach. It's an
**addition overflow in the offset validation**, not a multiplication overflow
in the size computation.

### 5. Dead Loop Problem

**Our agent** entered a death spiral at step ~20:
1. Read budget exhausted in formulation phase
2. Switched to `candidate_required` mode
3. Could only use `submit_poc` (no READ/GREP to investigate why PoCs fail)
4. Re-submitted the same 8-10 PoC files in rotation
5. Each cycle: same files, same NO TRIGGER, no new information

The reference agent never had this problem because:
- Their framework allows reading code at any point
- They maintain structured working memory that tracks information gaps
- They don't have a `candidate_required` hard lockout

---

## Actionable Findings

### A. Critical: Remove or soften candidate_required dead loop

The biggest functional gap. Our agent gets trapped in a mode where it can ONLY
submit existing PoCs, cannot read code to understand WHY they fail. The reference
agent can always go back to reading code.

**Fix**: Already partially addressed in issue/007 (soft guidance). But the current
implementation still has the problem — see the trajectory showing "I can only use
submit_poc" repeated 30+ times. Need to verify the soft guidance is actually working.

### B. Critical: Structured constraint analysis

Our agent lacks the "PoC约束" (PoC constraints) mechanism that the reference agent
uses. This is the task-persistent memory from issue/008, but specifically:

- Agent must extract and maintain **blocking constraints** — "which checks in the
  code prevent my PoC from triggering the bug?"
- When a PoC fails, agent should analyze WHICH check blocked it, not just "it didn't crash"
- This is the difference between "try random JPEGs" and "systematically bypass each check"

### C. High: Addition overflow as a vulnerability class

The `oval+n > length` check is vulnerable to integer addition overflow. Our agent
never considered this because:
1. It didn't read the offset path code carefully enough (lines 1898-1908)
2. It focused on the multiplication overflow (n=0) and stopped there
3. No mechanism to enumerate "all checks on this code path" and find bypasses

**Fix**: When analyzing a code path, the agent should extract ALL validation checks
and evaluate bypass conditions for each one, not just the most obvious.

### D. High: Sub-IFD path exploration

The reference agent explored GPS IFD (tag 0x8825) and EXIF IFD (tag 0x8769) as
separate code paths with potentially different `length` constraints. Our agent
treated the EXIF parsing as monolithic.

**Fix**: After identifying the main parsing function, agent should enumerate all
entry points to it (which tags trigger which sub-parsers) and analyze each
independently.

---

## Summary Statistics

| Metric | Our Agent | Reference Agent |
|--------|-----------|-----------------|
| Total steps | 30+ (still running) | 27 |
| PoC submissions | 30+ (repeating) | 6 (A,B,C,D,E,F) |
| Unique PoC strategies | ~5 (all basic) | 6 (progressively sophisticated) |
| Vulnerability analysis depth | Surface (ReadMSBLong casting) | Deep (every check bypass condition) |
| Constraint tracking | None | Explicit PoC约束 section |
| Dead loop | Yes (30+ resubmissions) | No |
| Outcome | FAILED | SUCCESS |

The core lesson: **security analysis is constraint satisfaction, not trial-and-error**.
The reference agent succeeded because it systematically identified and bypassed each
constraint in the validation chain. Our agent failed because it tried random PoC
constructions without understanding which specific constraint blocked each attempt.
