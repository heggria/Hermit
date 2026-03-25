// Tracks a DOM element's bounding rect for the tour spotlight overlay.

import { useCallback, useEffect, useRef, useState } from 'react';

interface ElementRect {
  readonly top: number;
  readonly left: number;
  readonly width: number;
  readonly height: number;
}

interface UseElementHighlightResult {
  readonly rect: ElementRect | null;
  readonly visible: boolean;
}

export function useElementHighlight(
  tourId: string | null,
): UseElementHighlightResult {
  const [rect, setRect] = useState<ElementRect | null>(null);
  const rafRef = useRef<number>(0);
  const observerRef = useRef<ResizeObserver | null>(null);

  const measure = useCallback((el: Element) => {
    const r = el.getBoundingClientRect();
    setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
  }, []);

  useEffect(() => {
    if (!tourId) {
      setRect(null);
      return;
    }

    const el = document.querySelector(`[data-tour-id="${tourId}"]`);
    if (!el) {
      setRect(null);
      return;
    }

    // Scroll into view
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // Initial measure after a short delay for scroll
    const timeout = setTimeout(() => measure(el), 150);

    // Track resize
    observerRef.current = new ResizeObserver(() => measure(el));
    observerRef.current.observe(el);

    // Track scroll and window resize
    const onScroll = () => {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => measure(el));
    };

    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);

    return () => {
      clearTimeout(timeout);
      cancelAnimationFrame(rafRef.current);
      observerRef.current?.disconnect();
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [tourId, measure]);

  return { rect, visible: rect !== null };
}
