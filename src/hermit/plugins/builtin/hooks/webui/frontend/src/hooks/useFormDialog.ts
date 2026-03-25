import { useState, useCallback, useEffect, useRef } from 'react';

interface MutationLike {
  readonly isPending: boolean;
  reset(): void;
}

interface UseFormDialogOptions<TForm> {
  /** Whether the dialog is currently open */
  readonly open: boolean;
  /** Callback to close the dialog */
  readonly onOpenChange: (open: boolean) => void;
  /** Returns the initial form state when the dialog opens */
  readonly initialValues: () => TForm;
  /** Mutations to track for isPending and to reset on open */
  readonly mutations?: readonly MutationLike[];
}

interface UseFormDialogReturn<TForm> {
  /** Current form values */
  readonly values: TForm;
  /** Update a single field */
  setField: <K extends keyof TForm>(key: K, value: TForm[K]) => void;
  /** Replace the entire form state */
  setValues: React.Dispatch<React.SetStateAction<TForm>>;
  /** Current error message (empty string when no error) */
  readonly error: string;
  /** Set an error message */
  setError: (msg: string) => void;
  /** Whether any tracked mutation is pending */
  readonly isPending: boolean;
  /** Wrap a submit callback: runs `fn`, and on thrown error sets the error state */
  handleSubmit: (fn: () => void | Promise<void>) => void;
}

/**
 * Encapsulates common form-dialog state management:
 * - form values reset when the dialog opens
 * - error state management
 * - mutation pending aggregation and reset
 * - submit wrapper with error catching
 */
export function useFormDialog<TForm>({
  open,
  initialValues,
  mutations = [],
}: UseFormDialogOptions<TForm>): UseFormDialogReturn<TForm> {
  const [values, setValues] = useState<TForm>(initialValues);
  const [error, setError] = useState('');

  // Keep a stable ref to initialValues so the effect doesn't re-run on every render
  const initialValuesRef = useRef(initialValues);
  initialValuesRef.current = initialValues;

  useEffect(() => {
    if (open) {
      setValues(initialValuesRef.current());
      setError('');
      for (const m of mutations) {
        m.reset();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const isPending = mutations.some((m) => m.isPending);

  const setField = useCallback(
    <K extends keyof TForm>(key: K, value: TForm[K]) => {
      setValues((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const handleSubmit = useCallback(
    (fn: () => void | Promise<void>) => {
      try {
        const result = fn();
        if (result instanceof Promise) {
          result.catch((err: unknown) => {
            setError(err instanceof Error ? err.message : String(err));
          });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [],
  );

  return {
    values,
    setField,
    setValues,
    error,
    setError,
    isPending,
    handleSubmit,
  };
}
