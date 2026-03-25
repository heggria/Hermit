// Task submission bar with auto-resize textarea, image upload/drag-drop, policy selector, and submit button.

import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, Loader2, Check, ImagePlus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useSubmitTask } from '@/api/hooks';

const POLICY_PROFILES = ['autonomous', 'supervised', 'default'] as const;
type PolicyProfile = (typeof POLICY_PROFILES)[number];

const MAX_ROWS = 3;
const LINE_HEIGHT_PX = 24;
const MAX_IMAGES = 5;
const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

interface ImageAttachment {
  readonly id: string;
  readonly name: string;
  readonly dataUrl: string;
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function TaskInputBar() {
  const { t } = useTranslation();
  const [description, setDescription] = useState('');
  const [policy, setPolicy] = useState<PolicyProfile>('autonomous');
  const [showSuccess, setShowSuccess] = useState(false);
  const [images, setImages] = useState<ImageAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const submitMutation = useSubmitTask();

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = MAX_ROWS * LINE_HEIGHT_PX;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  const addFiles = useCallback(async (files: FileList | File[]) => {
    const valid = Array.from(files).filter((f) => ACCEPTED_TYPES.includes(f.type));
    if (valid.length === 0) return;

    const newImages: ImageAttachment[] = [];
    for (const file of valid) {
      if (images.length + newImages.length >= MAX_IMAGES) break;
      const dataUrl = await fileToDataUrl(file);
      newImages.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: file.name,
        dataUrl,
      });
    }
    setImages((prev) => [...prev, ...newImages].slice(0, MAX_IMAGES));
  }, [images.length]);

  const removeImage = useCallback((id: string) => {
    setImages((prev) => prev.filter((img) => img.id !== id));
  }, []);

  const handleSubmit = useCallback(async () => {
    const trimmed = description.trim();
    if ((!trimmed && images.length === 0) || submitMutation.isPending) return;

    const attachments = images.map((img) => img.dataUrl);

    await submitMutation.mutateAsync({
      description: trimmed || t('controlCenter.imageOnlyTask'),
      policy_profile: policy,
      ...(attachments.length > 0 ? { attachments } : {}),
    });

    setDescription('');
    setImages([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 1500);
  }, [description, images, policy, submitMutation, t]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setDescription(e.target.value);
      adjustHeight();
    },
    [adjustHeight],
  );

  // Paste handler for images
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const files = Array.from(e.clipboardData.items)
        .filter((item) => item.kind === 'file' && ACCEPTED_TYPES.includes(item.type))
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null);
      if (files.length > 0) {
        e.preventDefault();
        addFiles(files);
      }
    },
    [addFiles],
  );

  // Drag-and-drop handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles],
  );

  const isDisabled = submitMutation.isPending || (!description.trim() && images.length === 0);

  return (
    <div
      className={cn(
        "border-b border-border/50 px-4 py-3 transition-colors",
        isDragging && "bg-primary/5 ring-1 ring-inset ring-primary/20",
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Image previews */}
      {images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {images.map((img) => (
            <div key={img.id} className="group relative">
              <img
                src={img.dataUrl}
                alt={img.name}
                className="size-12 rounded-md object-cover ring-1 ring-border"
              />
              <button
                type="button"
                onClick={() => removeImage(img.id)}
                className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-foreground/80 text-background opacity-0 transition-opacity group-hover:opacity-100"
              >
                <X className="size-2.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="flex-1 min-w-0">
          <textarea
            ref={textareaRef}
            value={description}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={t('controlCenter.inputPlaceholder')}
            disabled={submitMutation.isPending}
            rows={1}
            className="w-full resize-none bg-transparent text-sm leading-6 text-foreground placeholder:text-muted-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          <div className="flex items-center gap-2 mt-1">
            <Select value={policy} onValueChange={(v) => setPolicy(v as PolicyProfile)}>
              <SelectTrigger size="sm" className="h-6 min-w-[100px] text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {POLICY_PROFILES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {t(`controlCenter.policyOptions.${p}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Image upload button */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={images.length >= MAX_IMAGES}
              className="inline-flex size-6 items-center justify-center rounded text-muted-foreground/60 transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
              title={t('controlCenter.addImage')}
            >
              <ImagePlus className="size-3.5" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES.join(',')}
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </div>
        </div>
        <Button
          size="sm"
          disabled={isDisabled && !showSuccess}
          onClick={handleSubmit}
          className={cn(
            "h-8 px-3 shrink-0 transition-colors",
            showSuccess && "bg-emerald-500 hover:bg-emerald-500 text-white",
          )}
        >
          {submitMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : showSuccess ? (
            <Check className="size-3.5" />
          ) : (
            <Send className="size-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
}
