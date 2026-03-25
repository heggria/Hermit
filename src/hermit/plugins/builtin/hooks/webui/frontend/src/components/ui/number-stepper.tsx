import * as React from "react"
import { Minus, Plus } from "lucide-react"
import { cn } from "@/lib/utils"

interface NumberStepperProps {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  className?: string
}

function NumberStepper({
  value,
  onChange,
  min = 1,
  max = 99,
  step = 1,
  disabled = false,
  className,
}: NumberStepperProps) {
  const clamp = React.useCallback(
    (v: number) => Math.max(min, Math.min(max, v)),
    [min, max],
  )

  const handleInput = React.useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const parsed = parseInt(e.target.value, 10)
      if (!Number.isNaN(parsed)) onChange(clamp(parsed))
    },
    [onChange, clamp],
  )

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-lg border border-input bg-transparent",
        disabled && "opacity-50 pointer-events-none",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => onChange(clamp(value - step))}
        disabled={disabled || value <= min}
        className="flex size-8 items-center justify-center text-muted-foreground transition-colors hover:text-foreground disabled:opacity-30"
        aria-label="Decrease"
      >
        <Minus className="size-3.5" />
      </button>
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={value}
        onChange={handleInput}
        disabled={disabled}
        className="h-8 w-10 border-x border-input bg-transparent text-center text-sm font-medium text-foreground outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
      />
      <button
        type="button"
        onClick={() => onChange(clamp(value + step))}
        disabled={disabled || value >= max}
        className="flex size-8 items-center justify-center text-muted-foreground transition-colors hover:text-foreground disabled:opacity-30"
        aria-label="Increase"
      >
        <Plus className="size-3.5" />
      </button>
    </div>
  )
}

export { NumberStepper }
export type { NumberStepperProps }
