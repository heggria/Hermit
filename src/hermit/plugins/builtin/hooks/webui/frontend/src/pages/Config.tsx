import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Monitor,
  Puzzle,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useConfigStatus, useConfigPlugins } from "@/api/hooks";
import { ScrollArea } from "@/components/ui/scroll-area";

// ---------------------------------------------------------------------------
// Sidebar navigation sections
// ---------------------------------------------------------------------------

interface SettingsSection {
  readonly id: string;
  readonly labelKey: string;
  readonly icon: React.ElementType;
}

const SECTIONS: readonly SettingsSection[] = [
  { id: "general", labelKey: "config.general", icon: Monitor },
  { id: "plugins", labelKey: "config.plugins", icon: Puzzle },
  { id: "about", labelKey: "config.about", icon: Info },
] as const;

// ---------------------------------------------------------------------------
// Uptime formatter
// ---------------------------------------------------------------------------

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ---------------------------------------------------------------------------
// General section — system status
// ---------------------------------------------------------------------------

function GeneralSection() {
  const { t } = useTranslation();
  const { data, isLoading } = useConfigStatus();

  const items = data
    ? [
        { label: t("config.hostname"), value: data.host },
        { label: t("config.port"), value: String(data.port) },
        { label: t("config.pid"), value: String(data.pid) },
        { label: t("config.uptime"), value: formatUptime(data.uptime) },
      ]
    : [];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium text-foreground">
          {t("config.systemStatus")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("config.systemStatusDesc")}
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between py-2">
              <div className="h-4 w-20 animate-pulse rounded bg-muted" />
              <div className="h-4 w-32 animate-pulse rounded bg-muted" />
            </div>
          ))}
        </div>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">
          {t("config.statusError")}
        </p>
      ) : (
        <div className="rounded-lg border border-border">
          {items.map((item, i) => (
            <div
              key={item.label}
              className={cn(
                "flex items-center justify-between px-4 py-3",
                i < items.length - 1 && "border-b border-border"
              )}
            >
              <span className="text-sm text-muted-foreground">
                {item.label}
              </span>
              <span className="text-sm font-medium tabular-nums text-foreground">
                {item.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plugins section
// ---------------------------------------------------------------------------

function PluginsSection() {
  const { t } = useTranslation();
  const { data, isLoading } = useConfigPlugins();
  const plugins = data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium text-foreground">
          {t("config.plugins")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("config.pluginsDesc")}
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-12 animate-pulse rounded-lg bg-muted"
            />
          ))}
        </div>
      ) : plugins.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          {t("config.noPlugins")}
        </p>
      ) : (
        <div className="rounded-lg border border-border">
          {plugins.map((plugin, i) => (
            <div
              key={plugin.name}
              className={cn(
                "flex items-center justify-between gap-4 px-3 py-2",
                i < plugins.length - 1 && "border-b border-border"
              )}
            >
              <div className="flex shrink-0 items-center gap-2">
                <span className="text-[13px] font-medium text-foreground">
                  {plugin.name}
                </span>
                <span className="text-[11px] tabular-nums text-muted-foreground/50">
                  {plugin.version}
                </span>
                {plugin.builtin ? (
                  <span className="rounded-full bg-primary/10 px-1.5 py-px text-[10px] font-medium text-primary">
                    {t("config.pluginBuiltin")}
                  </span>
                ) : (
                  <span className="rounded-full bg-muted px-1.5 py-px text-[10px] font-medium text-muted-foreground">
                    {t("config.pluginUser")}
                  </span>
                )}
              </div>
              {plugin.description && (
                <span className="truncate text-[11px] text-muted-foreground/60">
                  {plugin.description}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// About section
// ---------------------------------------------------------------------------

function AboutSection() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium text-foreground">
          {t("config.about")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("config.aboutDesc")}
        </p>
      </div>

      <div className="rounded-lg border border-border">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="text-sm text-muted-foreground">
            {t("config.version")}
          </span>
          <span className="text-sm font-medium text-foreground">0.3.0</span>
        </div>
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-sm text-muted-foreground">
            {t("config.runtime")}
          </span>
          <span className="text-sm font-medium text-foreground">Python</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section content resolver
// ---------------------------------------------------------------------------

function SectionContent({
  sectionId,
}: {
  readonly sectionId: string;
}) {
  switch (sectionId) {
    case "general":
      return <GeneralSection />;
    case "plugins":
      return <PluginsSection />;
    case "about":
      return <AboutSection />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Settings dialog body — exported for use in Dialog wrapper
// ---------------------------------------------------------------------------

export default function Config() {
  const { t } = useTranslation();
  const [activeSection, setActiveSection] = useState("general");

  return (
    <div className="flex h-[520px] overflow-hidden">
      {/* Left sidebar */}
      <nav className="w-48 shrink-0 border-r border-border py-2 pr-2">
        <ScrollArea className="h-full">
          <div className="space-y-0.5 px-2">
            {SECTIONS.map((section) => {
              const isActive = activeSection === section.id;
              return (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => setActiveSection(section.id)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <section.icon
                    className={cn(
                      "size-4 shrink-0",
                      isActive
                        ? "text-foreground"
                        : "text-muted-foreground"
                    )}
                  />
                  <span>{t(section.labelKey)}</span>
                </button>
              );
            })}
          </div>
        </ScrollArea>
      </nav>

      {/* Right content */}
      <ScrollArea className="flex-1">
        <div className="px-6 py-4">
          <SectionContent sectionId={activeSection} />
        </div>
      </ScrollArea>
    </div>
  );
}
