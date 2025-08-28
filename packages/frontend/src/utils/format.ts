/**
 * Format a list of items into a human-readable string
 * @param items - Array of items to format
 * @param maxItems - Maximum number of items to show before truncating
 * @returns Formatted string
 */
export function formatList(items: string[], maxItems: number = 3): string {
  if (items.length === 0) return '';
  if (items.length === 1) return items[0];
  if (items.length <= maxItems) {
    return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
  }
  
  const visible = items.slice(0, maxItems);
  const remaining = items.length - maxItems;
  return visible.join(', ') + ` and ${remaining} more`;
}

/**
 * Truncate text to a specified length with ellipsis
 * @param text - Text to truncate
 * @param maxLength - Maximum length
 * @returns Truncated text
 */
export function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 3) + '...';
}

/**
 * Format search time in a human-readable way
 * @param timeMs - Time in milliseconds
 * @returns Formatted time string
 */
export function formatSearchTime(timeMs: number): string {
  if (timeMs < 1000) {
    return `${Math.round(timeMs)}ms`;
  }
  return `${(timeMs / 1000).toFixed(2)}s`;
}

/**
 * Format number of results
 * @param count - Number of results
 * @returns Formatted results string
 */
export function formatResultCount(count: number): string {
  if (count === 0) return 'No results';
  if (count === 1) return '1 result';
  if (count < 1000) return `${count} results`;
  if (count < 1000000) return `${(count / 1000).toFixed(1)}k results`;
  return `${(count / 1000000).toFixed(1)}M results`;
}

