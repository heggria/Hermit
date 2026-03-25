import { cn } from '@/lib/utils';

interface FilterTab<T extends string> {
  key: T;
  label: string;
  count?: number;
}

interface FilterTabsProps<T extends string> {
  /** Tab definitions */
  readonly tabs: readonly FilterTab<T>[];
  /** Currently active tab key */
  readonly activeTab: T;
  /** Callback when a tab is selected */
  readonly onChange: (tab: T) => void;
  /** Whether to show count badges */
  readonly showCount?: boolean;
  /** Size variant */
  readonly size?: 'sm' | 'default';
}

export function FilterTabs<T extends string>({
  tabs,
  activeTab,
  onChange,
  showCount = true,
  size = 'default',
}: FilterTabsProps<T>) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          onClick={() => onChange(tab.key)}
          className={cn(
            'rounded-full font-medium transition-all',
            size === 'sm'
              ? 'px-3 py-1 text-xs'
              : 'px-4 py-1.5 text-sm',
            activeTab === tab.key
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'bg-card text-muted-foreground hover:bg-muted border border-border',
          )}
        >
          {tab.label}
          {showCount && tab.count !== undefined && ` (${tab.count})`}
        </button>
      ))}
    </div>
  );
}
