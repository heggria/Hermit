import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCreateMcpServer } from "@/api/hooks";
import { RecommendedItemsList } from "@/components/ui/RecommendedItemsList";

// ---------------------------------------------------------------------------
// Recommended MCP server presets
// ---------------------------------------------------------------------------

interface ServerPresetAuth {
  type: "api_key" | "oauth";
  env_key?: string;
  token_url?: string;
}

interface ServerPreset {
  name: string;
  description_en: string;
  description_zh: string;
  transport: "stdio" | "http";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  auth?: ServerPresetAuth;
}

const PRESETS: readonly ServerPreset[] = [
  {
    name: "linear",
    description_en: "Integrate Linear issue tracking and project management",
    description_zh: "集成 Linear 的问题追踪和项目管理功能",
    transport: "stdio",
    command: "npx",
    args: ["-y", "mcp-remote", "https://mcp.linear.app/sse"],
  },
  {
    name: "notion",
    description_en: "Read docs, update pages, and manage tasks",
    description_zh: "阅读文档、更新页面、管理任务",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@notionhq/notion-mcp-server"],
    env: { OPENAPI_MCP_HEADERS: '{"Authorization":"Bearer ntn_xxx","Notion-Version":"2022-06-28"}' },
    auth: { type: "api_key", env_key: "OPENAPI_MCP_HEADERS", token_url: "https://www.notion.so/profile/integrations" },
  },
  {
    name: "figma",
    description_en: "Generate better code with full Figma design context",
    description_zh: "通过引入完整的 Figma 设计背景信息来生成更优质的代码",
    transport: "stdio",
    command: "npx",
    args: ["-y", "figma-developer-mcp", "--stdio"],
    env: { FIGMA_API_KEY: "" },
    auth: { type: "api_key", env_key: "FIGMA_API_KEY", token_url: "https://www.figma.com/developers/api#access-tokens" },
  },
  {
    name: "playwright",
    description_en: "Browser automation for designing and testing user interfaces",
    description_zh: "集成浏览器自动化功能以设计和测试用户界面",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@playwright/mcp@latest"],
  },
  {
    name: "filesystem",
    description_en: "Read, write, and manage local files and directories",
    description_zh: "读写和管理本地文件与目录",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."],
  },
  {
    name: "github",
    description_en: "GitHub repos, issues, PRs, and code search",
    description_zh: "GitHub 仓库、Issue、PR 和代码搜索",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    env: { GITHUB_PERSONAL_ACCESS_TOKEN: "" },
    auth: { type: "api_key", env_key: "GITHUB_PERSONAL_ACCESS_TOKEN", token_url: "https://github.com/settings/tokens" },
  },
  {
    name: "fetch",
    description_en: "Fetch and extract content from any URL",
    description_zh: "抓取和提取任意 URL 的内容",
    transport: "stdio",
    command: "uvx",
    args: ["mcp-server-fetch"],
  },
  {
    name: "memory",
    description_en: "Persistent knowledge graph for long-term memory",
    description_zh: "基于知识图谱的持久化长期记忆",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-memory"],
  },
  {
    name: "postgres",
    description_en: "Query and manage PostgreSQL databases",
    description_zh: "查询和管理 PostgreSQL 数据库",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-postgres"],
    env: { POSTGRES_CONNECTION_STRING: "" },
    auth: { type: "api_key", env_key: "POSTGRES_CONNECTION_STRING" },
  },
  {
    name: "brave-search",
    description_en: "Web search via Brave Search API",
    description_zh: "通过 Brave Search API 进行网络搜索",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-brave-search"],
    env: { BRAVE_API_KEY: "" },
    auth: { type: "api_key", env_key: "BRAVE_API_KEY", token_url: "https://brave.com/search/api/" },
  },
  {
    name: "puppeteer",
    description_en: "Browser automation, screenshots, and web scraping",
    description_zh: "浏览器自动化、截图与网页抓取",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-puppeteer"],
  },
  {
    name: "sqlite",
    description_en: "Query and manage SQLite databases",
    description_zh: "查询和管理 SQLite 数据库",
    transport: "stdio",
    command: "uvx",
    args: ["mcp-server-sqlite", "--db-path", ""],
  },
] as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface RecommendedServersProps {
  readonly installedNames: ReadonlySet<string>;
}

export function RecommendedServers({ installedNames }: RecommendedServersProps) {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language.startsWith("zh");
  const createMutation = useCreateMcpServer();
  const [justInstalled, setJustInstalled] = useState<Set<string>>(new Set());

  const mergedInstalled = new Set([...installedNames, ...justInstalled]);

  // Only show presets that are not already installed
  const available = PRESETS.filter((p) => !mergedInstalled.has(p.name));
  const installed = PRESETS.filter((p) => mergedInstalled.has(p.name));
  const all = [...available, ...installed];

  function handleInstall(preset: ServerPreset) {
    createMutation.mutate(
      {
        name: preset.name,
        transport: preset.transport,
        description: isZh ? preset.description_zh : preset.description_en,
        command: preset.command,
        args: preset.args ? [...preset.args] : undefined,
        env: preset.env ? { ...preset.env } : undefined,
        url: preset.url,
        auth: preset.auth ? { ...preset.auth } : undefined,
      },
      {
        onSuccess: () => {
          setJustInstalled((prev) => new Set([...prev, preset.name]));
        },
        onError: (err: Error) => {
          // 409 means server already exists in config — treat as installed
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
      title={t("mcpServers.recommended.title")}
      installLabel={t("mcpServers.recommended.install")}
    />
  );
}
