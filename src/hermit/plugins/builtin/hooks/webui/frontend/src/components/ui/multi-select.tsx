import { useState, useRef, useCallback, useEffect, type KeyboardEvent } from 'react';
import { Check, ChevronsUpDown, X, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export interface MultiSelectOption {
  readonly value: string;
  readonly label: string;
  readonly description?: string;
}

interface MultiSelectProps {
  readonly options: readonly MultiSelectOption[];
  readonly selected: readonly string[];
  readonly onSelectedChange: (selected: readonly string[]) => void;
  readonly placeholder?: string;
  readonly emptyText?: string;
  readonly customHint?: string;
  readonly isLoading?: boolean;
  readonly allowCustom?: boolean;
}

export function MultiSelect({
  options,
  selected,
  onSelectedChange,
  placeholder = 'Select...',
  emptyText = 'No options available',
  customHint,
  isLoading = false,
  allowCustom = false,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = search
    ? options.filter(
        (o) =>
          o.label.toLowerCase().includes(search.toLowerCase()) ||
          o.value.toLowerCase().includes(search.toLowerCase()) ||
          (o.description?.toLowerCase().includes(search.toLowerCase()) ?? false),
      )
    : options;

  // Whether the current search text is a candidate for custom entry
  const trimmed = search.trim();
  const canAddCustom =
    allowCustom &&
    trimmed.length > 0 &&
    !selected.includes(trimmed) &&
    !options.some((o) => o.value === trimmed);

  const toggle = useCallback(
    (value: string) => {
      if (selected.includes(value)) {
        onSelectedChange(selected.filter((s) => s !== value));
      } else {
        onSelectedChange([...selected, value]);
      }
    },
    [selected, onSelectedChange],
  );

  const addCustom = useCallback(() => {
    if (!trimmed || selected.includes(trimmed)) return;
    onSelectedChange([...selected, trimmed]);
    setSearch('');
  }, [trimmed, selected, onSelectedChange]);

  const remove = useCallback(
    (value: string) => {
      onSelectedChange(selected.filter((s) => s !== value));
    },
    [selected, onSelectedChange],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (canAddCustom) {
          addCustom();
        }
      }
      if (e.key === 'Backspace' && search === '' && selected.length > 0) {
        onSelectedChange(selected.slice(0, -1));
      }
    },
    [canAddCustom, addCustom, search, selected, onSelectedChange],
  );

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch('');
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger */}
      <button
        type="button"
        onClick={() => {
          setOpen(!open);
          if (!open) {
            setTimeout(() => inputRef.current?.focus(), 0);
          }
        }}
        className={cn(
          'flex w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm',
          'hover:bg-accent/50 transition-colors',
          open && 'ring-2 ring-ring ring-offset-1',
        )}
      >
        <span className="text-muted-foreground truncate">
          {selected.length > 0
            ? `${selected.length} selected`
            : placeholder}
        </span>
        <ChevronsUpDown className="ml-2 size-4 shrink-0 text-muted-foreground" />
      </button>

      {/* Selected badges */}
      {selected.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {selected.map((value) => {
            const opt = options.find((o) => o.value === value);
            const isCustom = !opt;
            return (
              <Badge
                key={value}
                variant={isCustom ? 'outline' : 'secondary'}
                className="gap-1 pr-1"
              >
                {opt?.label ?? value}
                {isCustom && (
                  <span className="text-[9px] text-muted-foreground ml-0.5">(custom)</span>
                )}
                <button
                  type="button"
                  onClick={() => remove(value)}
                  className="rounded-full p-0.5 hover:bg-muted-foreground/20"
                >
                  <X className="size-3" />
                </button>
              </Badge>
            );
          })}
        </div>
      )}

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-border bg-popover shadow-lg">
          {/* Search input */}
          <div className="border-b border-border p-2">
            <input
              ref={inputRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={allowCustom ? (customHint ?? placeholder) : placeholder}
              className="w-full bg-transparent px-1 py-1 text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          {/* Options list */}
          <div className="max-h-48 overflow-y-auto p-1">
            {isLoading ? (
              <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                Loading...
              </div>
            ) : (
              <>
                {/* Add custom entry */}
                {canAddCustom && (
                  <button
                    type="button"
                    onClick={addCustom}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors',
                      'hover:bg-accent hover:text-accent-foreground',
                      'text-primary',
                    )}
                  >
                    <Plus className="size-4 shrink-0" />
                    <span className="font-medium">&quot;{trimmed}&quot;</span>
                  </button>
                )}

                {filtered.length === 0 && !canAddCustom ? (
                  <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                    {search ? 'No matches found' : emptyText}
                  </div>
                ) : (
                  filtered.map((option) => {
                    const isSelected = selected.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => toggle(option.value)}
                        className={cn(
                          'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors',
                          'hover:bg-accent hover:text-accent-foreground',
                          isSelected && 'bg-accent/50',
                        )}
                      >
                        <div
                          className={cn(
                            'flex size-4 shrink-0 items-center justify-center rounded border',
                            isSelected
                              ? 'border-primary bg-primary text-primary-foreground'
                              : 'border-input',
                          )}
                        >
                          {isSelected && <Check className="size-3" />}
                        </div>
                        <div className="min-w-0 flex-1">
                          <span className="font-medium">{option.label}</span>
                          {option.description && (
                            <p className="truncate text-[11px] text-muted-foreground">
                              {option.description}
                            </p>
                          )}
                        </div>
                      </button>
                    );
                  })
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
