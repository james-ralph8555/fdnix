import { createSignal, createEffect, onCleanup } from 'solid-js';

/**
 * Creates a debounced signal that delays updating until after the specified delay
 * @param initialValue - Initial value for the signal
 * @param delay - Delay in milliseconds (default: 1000)
 * @returns Tuple of [debouncedValue, setValue]
 */
export function createDebouncedSignal<T>(initialValue: T, delay: number = 1000) {
  const [value, setValue] = createSignal<T>(initialValue);
  const [debouncedValue, setDebouncedValue] = createSignal<T>(initialValue);
  
  createEffect(() => {
    const currentValue = value();
    const timeoutId = setTimeout(() => {
      setDebouncedValue(() => currentValue);
    }, delay);
    
    onCleanup(() => clearTimeout(timeoutId));
  });
  
  return [debouncedValue, setValue] as const;
}

/**
 * Traditional debounce function for callbacks
 * @param func - Function to debounce
 * @param delay - Delay in milliseconds
 * @returns Debounced function
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeoutId: number | undefined;
  
  return (...args: Parameters<T>) => {
    clearTimeout(timeoutId);
    timeoutId = window.setTimeout(() => func(...args), delay);
  };
}