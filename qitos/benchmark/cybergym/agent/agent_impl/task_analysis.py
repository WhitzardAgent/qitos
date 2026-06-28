"""Task-description analysis helpers for CyberGym."""

from __future__ import annotations

import re


class TaskAnalysisMixin:
    """Deterministic parsing helpers for task descriptions."""

    @staticmethod
    def _extract_cve_id(description: str) -> str:
        match = re.search(r'CVE-\d{4}-\d{4,}', description, re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _classify_bug_type(description: str) -> str:
        desc_lower = description.lower()
        bug_patterns = {
            "buffer_overflow": [
                "buffer overflow", "stack overflow", "heap overflow",
                "stack-based buffer", "heap-based buffer", "out-of-bounds write",
                "out-of-bounds read", "oob write", "oob read",
            ],
            "use_after_free": [
                "use-after-free", "use after free", "uaf",
                "double free", "heap-use-after-free",
            ],
            "integer_overflow": [
                "integer overflow", "integer underflow", "signed integer",
                "unsigned integer", "arithmetic overflow",
            ],
            "null_pointer_dereference": [
                "null pointer", "null dereference", "nullptr",
                "segfault", "segmentation fault",
            ],
            "format_string": ["format string", "format-string"],
            "race_condition": [
                "race condition", "data race", "race-condition",
                "concurrent", "toctou",
            ],
            "command_injection": [
                "command injection", "code injection", "rce",
                "remote code execution",
            ],
            "xss": ["cross-site scripting", "xss"],
            "sql_injection": ["sql injection", "sql-injection"],
            # P46: additional common vulnerability classes
            "type_confusion": [
                "type confusion", "type-confusion", "incorrect type",
                "invalid cast", "bad cast",
            ],
            "uninitialized_value": [
                "uninitialized", "use of uninitialized", "uninitialised",
                "uninitialized value", "uninitialized memory",
            ],
            "information_disclosure": [
                "information disclosure", "info leak", "information leak",
                "memory leak", "data leak", "sensitive information",
            ],
            "denial_of_service": [
                "denial of service", "dos", "infinite loop",
                "resource exhaustion", "cpu exhaustion", "oom",
                "out of memory",
            ],
            "privilege_escalation": [
                "privilege escalation", "privilege escalation",
                "elevation of privilege", "privilege elevation",
            ],
            "logic_bug": [
                "incorrect calculation", "incorrect comparison",
                "logic error", "logic bug", "wrong calculation",
                "incorrect behavior", "miscalculation",
            ],
        }
        for bug_type, patterns in bug_patterns.items():
            for pattern in patterns:
                if pattern in desc_lower:
                    return bug_type
        # P46: fallback classification based on ASAN/UBSAN keywords
        asan_keywords = [
            "addresssanitizer", "heap-buffer", "stack-buffer",
            "use-after-free", "heap-use-after", "out-of-bounds",
        ]
        ubsan_keywords = [
            "undefinedbehaviorsanitizer", "undefined behavior",
            "runtime error:", "signed integer overflow",
        ]
        if any(kw in desc_lower for kw in asan_keywords):
            return "memory_corruption"
        if any(kw in desc_lower for kw in ubsan_keywords):
            return "undefined_behavior"
        # Generic fallback for descriptions mentioning calculation/comparison
        if any(kw in desc_lower for kw in ("calculation", "comparison", "incorrect")):
            return "logic_bug"
        return ""

    @staticmethod
    def _extract_affected_component(description: str) -> str:
        patterns = [
            r'in\s+the\s+(\w+)\s+(?:function|module|component|handler)',
            r'in\s+(\w+)\s+(?:before|when|while|during)',
            r'(\w+)\s+(?:function|module|handler)\s+(?:does not|fails)',
            r'affected\s+(?:function|module|component):\s*(\w+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""
