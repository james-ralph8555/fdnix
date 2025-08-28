import { createSignal, createEffect, onCleanup } from 'solid-js';

interface SearchInputProps {
  onSearch: (query: string) => void;
  placeholder?: string;
  debounceTime?: number;
  initialValue?: string;
  loading?: boolean;
}

export function SearchInput(props: SearchInputProps) {
  const {
    onSearch,
    placeholder = 'Search packages in Nixpkgs...',
    debounceTime = 1000,
    initialValue = '',
    loading = false,
  } = props;

  const [inputValue, setInputValue] = createSignal(initialValue);
  let debounceTimeout: number | undefined;

  const triggerDebouncedSearch = (value: string) => {
    // Clear existing timeout
    if (debounceTimeout) {
      clearTimeout(debounceTimeout);
    }
    
    // Set new timeout
    debounceTimeout = setTimeout(() => {
      const query = value.trim();
      if (query.length > 0) {
        onSearch(query);
      }
    }, debounceTime);
  };

  const handleInput = (event: Event) => {
    const target = event.target as HTMLInputElement;
    const value = target.value;
    setInputValue(value);
    
    // Trigger debounced search
    triggerDebouncedSearch(value);
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const query = inputValue().trim();
      if (query.length > 0) {
        // Clear any pending debounced search and trigger immediately
        if (debounceTimeout) {
          clearTimeout(debounceTimeout);
        }
        onSearch(query);
      }
    }
  };

  const handleClear = () => {
    if (debounceTimeout) {
      clearTimeout(debounceTimeout);
    }
    setInputValue('');
  };

  return (
    <div class="relative w-full max-w-2xl mx-auto">
      <div class="relative">
        {/* Search Icon */}
        <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <svg
            class="h-5 w-5 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>

        {/* Input Field */}
        <input
          type="text"
          class="search-input pl-10 pr-12"
          placeholder={placeholder}
          value={inputValue()}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          autocomplete="off"
          spellcheck={false}
        />

        {/* Loading Spinner or Clear Button */}
        <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
          {loading ? (
            <div class="animate-spin h-5 w-5 border-2 border-nix-blue border-t-transparent rounded-full"></div>
          ) : inputValue().length > 0 ? (
            <button
              type="button"
              class="text-gray-400 hover:text-gray-600 focus:outline-none"
              onClick={handleClear}
              aria-label="Clear search"
            >
              <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          ) : null}
        </div>
      </div>

      {/* Search Tips */}
      {inputValue().length === 0 && (
        <div class="mt-3 text-sm text-gray-500 text-center">
          <p>
            Try searching for packages like "firefox", "python", or "nodejs"
          </p>
          <p class="mt-1">
            Use filters to narrow down by license, category, or platform
          </p>
        </div>
      )}

      {/* Loading Indicator */}
      {loading && inputValue().length > 0 && (
        <div class="mt-2 text-xs text-gray-400 text-center">
          Searching...
        </div>
      )}
    </div>
  );
}
