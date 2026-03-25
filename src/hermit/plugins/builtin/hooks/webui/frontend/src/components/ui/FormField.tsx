import type { ReactNode } from 'react';

interface FormFieldProps {
  /** Field label text */
  readonly label: string;
  /** The input element (Input, Textarea, Select, MultiSelect, NumberStepper, etc.) */
  readonly children: ReactNode;
}

export function FormField({ label, children }: FormFieldProps) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium text-foreground">{label}</label>
      {children}
    </div>
  );
}
