import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCreateSkill } from "@/api/hooks";
import { RecommendedItemsList } from "@/components/ui/RecommendedItemsList";

// ---------------------------------------------------------------------------
// Recommended skill presets — SkillHub trending picks
// ---------------------------------------------------------------------------

interface SkillPreset {
  name: string;
  description_en: string;
  description_zh: string;
  content: string;
  max_tokens?: number;
}

const PRESETS: readonly SkillPreset[] = [
  {
    name: "code-review",
    description_en: "Systematic code review covering quality, security, and maintainability",
    description_zh: "系统性代码审查，覆盖质量、安全性和可维护性",
    content: [
      "Review code changes for:",
      "- Correctness and edge cases",
      "- Security vulnerabilities (injection, XSS, secrets)",
      "- Performance anti-patterns",
      "- Error handling completeness",
      "- Naming and readability",
      "",
      "Output severity-ranked findings: CRITICAL > HIGH > MEDIUM > LOW.",
    ].join("\n"),
  },
  {
    name: "git-workflow",
    description_en: "Conventional commits, branch strategy, and PR best practices",
    description_zh: "约定式提交、分支策略和 PR 最佳实践",
    content: [
      "Commit format: <type>: <description>",
      "Types: feat, fix, refactor, docs, test, chore, perf, ci",
      "",
      "Branch naming: feature/xxx, fix/xxx, chore/xxx",
      "PR: comprehensive summary, test plan, keep under 400 lines.",
    ].join("\n"),
  },
  {
    name: "api-design",
    description_en: "REST API design with consistent conventions and error handling",
    description_zh: "REST API 设计，统一规范和错误处理",
    content: [
      "Design REST APIs following:",
      "- Resource-oriented URLs (nouns, not verbs)",
      "- Consistent envelope: { data, error, meta }",
      "- Proper HTTP status codes (200/201/204/400/404/409/422/500)",
      "- Pagination: cursor-based preferred, offset as fallback",
      "- Validate all inputs at the boundary with schema validation",
    ].join("\n"),
  },
  {
    name: "testing-strategy",
    description_en: "TDD workflow with unit, integration, and E2E test coverage",
    description_zh: "TDD 工作流，涵盖单元、集成和端到端测试",
    content: [
      "Follow TDD: RED → GREEN → REFACTOR",
      "- Unit tests for pure logic and utilities",
      "- Integration tests for API endpoints and DB operations",
      "- E2E tests for critical user flows",
      "- Target 80%+ coverage",
      "- Never mock what you don't own",
    ].join("\n"),
  },
  {
    name: "security-audit",
    description_en: "OWASP Top 10 vulnerability detection and remediation",
    description_zh: "OWASP Top 10 漏洞检测与修复",
    content: [
      "Check for:",
      "- SQL injection (use parameterized queries)",
      "- XSS (sanitize all user-rendered content)",
      "- CSRF protection on state-changing endpoints",
      "- Hardcoded secrets (API keys, tokens, passwords)",
      "- Insecure deserialization",
      "- Broken access control",
      "",
      "Flag CRITICAL issues immediately. Stop and fix before continuing.",
    ].join("\n"),
  },
  {
    name: "task-decomposition",
    description_en: "Break complex goals into atomic, parallelizable subtasks",
    description_zh: "将复杂目标拆解为原子化、可并行的子任务",
    content: [
      "Decompose complex tasks:",
      "1. Identify independent work units",
      "2. Define clear inputs/outputs for each subtask",
      "3. Mark dependencies (A blocks B)",
      "4. Maximize parallelism — independent tasks run concurrently",
      "5. Each subtask should be completable in one session",
    ].join("\n"),
  },
  {
    name: "documentation",
    description_en: "Generate clear, structured documentation for code and APIs",
    description_zh: "为代码和 API 生成清晰、结构化的文档",
    content: [
      "Write documentation that:",
      "- Leads with the WHY, then HOW",
      "- Includes runnable code examples",
      "- Documents edge cases and gotchas",
      "- Keeps API docs in sync with implementation",
      "- Uses consistent heading hierarchy",
    ].join("\n"),
  },
  {
    name: "refactoring",
    description_en: "Safe, incremental refactoring with automated verification",
    description_zh: "安全、增量式重构，配合自动化验证",
    content: [
      "Refactoring workflow:",
      "1. Ensure tests exist before refactoring",
      "2. Make one small change at a time",
      "3. Run tests after each change",
      "4. Prefer extracting over rewriting",
      "5. Keep functions < 50 lines, files < 800 lines",
      "6. No behavior changes — only structure improvements",
    ].join("\n"),
  },
  {
    name: "debugging",
    description_en: "Systematic debugging with hypothesis-driven investigation",
    description_zh: "假设驱动的系统化调试方法",
    content: [
      "Debug systematically:",
      "1. Reproduce the issue reliably",
      "2. Read the error message and stack trace carefully",
      "3. Form a hypothesis about root cause",
      "4. Add targeted logging or breakpoints to verify",
      "5. Fix the root cause, not symptoms",
      "6. Add a regression test",
    ].join("\n"),
  },
  {
    name: "performance-optimization",
    description_en: "Profile-first performance optimization with measurable results",
    description_zh: "以性能分析为先导的优化，确保可量化的效果",
    content: [
      "Optimize with evidence:",
      "1. Profile before optimizing — never guess",
      "2. Identify the bottleneck (CPU, I/O, memory, network)",
      "3. Optimize the hot path only",
      "4. Benchmark before and after",
      "5. Common wins: batching, caching, lazy loading, query optimization",
    ].join("\n"),
  },
  {
    name: "data-pipeline",
    description_en: "ETL and data transformation patterns with validation",
    description_zh: "ETL 和数据转换模式，含数据验证",
    content: [
      "Data pipeline principles:",
      "- Validate schema at ingestion boundary",
      "- Idempotent transforms (safe to re-run)",
      "- Handle nulls, duplicates, and encoding issues explicitly",
      "- Log row counts at each stage for reconciliation",
      "- Fail fast on schema violations, not silently",
    ].join("\n"),
  },
  {
    name: "prompt-engineering",
    description_en: "Effective prompting patterns for LLM-powered features",
    description_zh: "面向 LLM 功能的高效提示词设计模式",
    content: [
      "Prompt design patterns:",
      "- Be specific: include format, constraints, and examples",
      "- Use system prompts for persona and rules",
      "- Chain-of-thought for complex reasoning",
      "- Few-shot examples for consistent output format",
      "- Guard against injection in user-facing prompts",
    ].join("\n"),
  },
] as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface RecommendedSkillsProps {
  readonly installedNames: ReadonlySet<string>;
}

export function RecommendedSkills({ installedNames }: RecommendedSkillsProps) {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language.startsWith("zh");
  const createMutation = useCreateSkill();
  const [justInstalled, setJustInstalled] = useState<Set<string>>(new Set());

  const mergedInstalled = new Set([...installedNames, ...justInstalled]);

  const available = PRESETS.filter((p) => !mergedInstalled.has(p.name));
  const installed = PRESETS.filter((p) => mergedInstalled.has(p.name));
  const all = [...available, ...installed];

  function handleInstall(preset: SkillPreset) {
    createMutation.mutate(
      {
        name: preset.name,
        description: isZh ? preset.description_zh : preset.description_en,
        content: preset.content,
        max_tokens: preset.max_tokens,
      },
      {
        onSuccess: () => {
          setJustInstalled((prev) => new Set([...prev, preset.name]));
        },
        onError: (err: Error) => {
          if (err.message.includes("already exists")) {
            setJustInstalled((prev) => new Set([...prev, preset.name]));
          }
        },
      },
    );
  }

  return (
    <RecommendedItemsList
      items={all}
      installedNames={mergedInstalled}
      onInstall={handleInstall}
      installingName={createMutation.variables?.name}
      isInstalling={createMutation.isPending}
      isZh={isZh}
      title={t("skills.recommended.title")}
      installLabel={t("skills.recommended.install")}
    />
  );
}
