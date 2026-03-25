import { Check, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Generic recommended-items list — shared rendering for skills, MCP servers,
// and any future preset-based "install from catalogue" UI.
// ---------------------------------------------------------------------------

/** Minimum shape every preset item must satisfy. */
export interface RecommendedItem {
  readonly name: string;
  readonly description_en: string;
  readonly description_zh: string;
}

export interface RecommendedItemsListProps<T extends RecommendedItem> {
  /** Full list of preset items (available + already installed). */
  readonly items: readonly T[];
  /** Names that are already installed (externally or just-now). */
  readonly installedNames: ReadonlySet<string>;
  /** Called when the user clicks "Install" on a non-installed item. */
  readonly onInstall: (item: T) => void;
  /** The name of the item currently being installed (if any). */
  readonly installingName: string | undefined;
  /** Whether the install mutation is in-flight. */
  readonly isInstalling: boolean;
  /** Whether to use Chinese descriptions. */
  readonly isZh: boolean;
  /** Translated title shown above the list. */
  readonly title: string;
  /** Translated label for the install button. */
  readonly installLabel: string;
}

export function RecommendedItemsList<T extends RecommendedItem>({
  items,
  installedNames,
  onInstall,
  installingName,
  isInstalling,
  isZh,
  title,
  installLabel,
}: RecommendedItemsListProps<T>) {
  if (items.length === 0) return null;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <div className="divide-y divide-border rounded-xl border border-border">
        {items.map((item) => {
          const installed = installedNames.has(item.name);
          const installing =
            isInstalling && installingName === item.name;

          return (
            <div
              key={item.name}
              className="flex items-center gap-3 px-4 py-3"
            >
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
                {item.name.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground">
                  {item.name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {isZh ? item.description_zh : item.description_en}
                </p>
              </div>
              {installed ? (
                <span className="flex items-center gap-1 text-xs text-emerald-600">
                  <Check className="size-3.5" />
                </span>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 shrink-0 text-xs"
                  disabled={installing}
                  onClick={() => onInstall(item)}
                >
                  {installing ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <>
                      <Plus className="size-3" />
                      {installLabel}
                    </>
                  )}
                </Button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
