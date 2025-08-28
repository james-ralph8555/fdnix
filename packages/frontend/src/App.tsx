import { createSignal, createEffect, onMount, Show } from 'solid-js';
import type { SearchState, SearchFilters } from './types';
import { apiClient } from './services/api';
import { formatResultCount, formatSearchTime } from './utils/format';
import { SearchInput } from './components/SearchInput';
import { FilterPanel } from './components/FilterPanel';
import { SearchResults } from './components/SearchResults';
import { LoadingSpinner } from './components/LoadingSpinner';
import { PaginationControls } from './components/PaginationControls';

function App() {
  // Application settings
  const [debounceTime, setDebounceTime] = createSignal(1000);
  const [pageSize] = createSignal(20);

  // Search state
  const [searchState, setSearchState] = createSignal<SearchState>({
    query: '',
    results: [],
    total: 0,
    loading: false,
    error: null,
    currentPage: 1,
    pageSize: pageSize(),
    filters: {
      license: '',
      category: '',
      showBroken: false,
      showUnfree: false,
    },
  });

  // UI state
  const [searchTime, setSearchTime] = createSignal<number | null>(null);
  const [apiHealthy, setApiHealthy] = createSignal<boolean | null>(null);
  
  // Request cancellation
  let currentAbortController: AbortController | null = null;

  // API health check function
  const checkApiHealth = async () => {
    console.log('API Base URL:', apiClient.getBaseUrl());
    try {
      await apiClient.healthCheck();
      setApiHealthy(true);
    } catch (error) {
      console.warn('API health check failed:', error);
      console.warn('API URL being used:', apiClient.getBaseUrl());
      setApiHealthy(false);
    }
  };

  // Check API health on mount
  onMount(() => {
    checkApiHealth();
  });

  const performSearch = async (query: string, page: number = 1, filters?: SearchFilters) => {
    const currentState = searchState();
    const currentFilters = filters || currentState.filters;

    // Don't search if query is empty
    if (!query.trim()) {
      setSearchState({
        ...currentState,
        query: '',
        results: [],
        total: 0,
        loading: false,
        error: null,
        currentPage: 1,
      });
      return;
    }

    // Cancel any existing request
    if (currentAbortController) {
      console.log('Cancelling previous request');
      currentAbortController.abort();
    }
    
    // Create new abort controller for this request
    currentAbortController = new AbortController();

    // Don't search if API is known to be unhealthy
    if (apiHealthy() === false) {
      setSearchState({
        ...currentState,
        loading: false,
        error: 'API is currently unavailable. Please try again later.',
        results: [],
        total: 0,
        query: query.trim(),
        currentPage: page,
        filters: currentFilters,
      });
      return;
    }

    // Set loading state
    setSearchState({
      ...currentState,
      loading: true,
      error: null,
      query: query.trim(),
      currentPage: page,
      filters: currentFilters,
    });

    try {
      const startTime = Date.now();
      const response = await apiClient.search({
        q: query.trim(),
        limit: pageSize(),
        offset: (page - 1) * pageSize(),
        license: currentFilters.license || undefined,
        category: currentFilters.category || undefined,
      }, currentAbortController.signal);
      const endTime = Date.now();

      // Clear the abort controller since request completed
      currentAbortController = null;
      setSearchTime(response.query_time_ms);
      setSearchState({
        ...searchState(),
        results: response.packages,
        total: response.total_count,
        loading: false,
        error: null,
      });
    } catch (error) {
      // If request was aborted, don't update state
      if (error instanceof Error && error.name === 'AbortError') {
        console.log('Request was aborted');
        return;
      }

      currentAbortController = null;
      console.error('Search failed:', error);
      setSearchState({
        ...searchState(),
        loading: false,
        error: error instanceof Error ? error.message : 'An unexpected error occurred',
        results: [],
        total: 0,
      });
      setSearchTime(null);
    }
  };

  const handleSearch = (query: string) => {
    performSearch(query, 1);
  };

  const handlePageChange = (page: number) => {
    const currentState = searchState();
    if (currentState.query) {
      performSearch(currentState.query, page, currentState.filters);
    }
  };

  const handleFiltersChange = (filters: SearchFilters) => {
    const currentState = searchState();
    if (currentState.query) {
      performSearch(currentState.query, 1, filters);
    } else {
      setSearchState({
        ...currentState,
        filters,
        currentPage: 1,
      });
    }
  };

  const handleDebounceTimeChange = (time: number) => {
    setDebounceTime(Math.max(100, Math.min(5000, time))); // Clamp between 100ms and 5s
  };

  const handleRetry = async () => {
    // First check API health, then retry the search
    await checkApiHealth();
    const currentState = searchState();
    if (currentState.query && apiHealthy() !== false) {
      performSearch(currentState.query, currentState.currentPage, currentState.filters);
    }
  };

  return (
    <div class="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      {/* Header */}
      <header class="bg-white shadow-sm border-b border-gray-200">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div class="flex items-center justify-between h-16">
            <div class="flex items-center">
              <div class="flex-shrink-0">
                <h1 class="text-2xl font-bold text-nix-blue">fdnix</h1>
              </div>
              <div class="ml-4 text-sm text-gray-500">
                A searchable index of the Nixpkgs repository
              </div>
            </div>
            
            {/* Settings */}
            <div class="flex items-center gap-4">
              <div class="hidden sm:block text-sm">
                <label class="text-gray-600 mr-2">Debounce:</label>
                <input
                  type="range"
                  min="100"
                  max="3000"
                  step="100"
                  value={debounceTime()}
                  onInput={(e) => handleDebounceTimeChange(parseInt(e.target.value))}
                  class="w-20"
                />
                <span class="ml-2 text-gray-500 font-mono text-xs">
                  {debounceTime()}ms
                </span>
              </div>
              
              {/* API Status */}
              <div class="flex items-center">
                <div
                  class={`w-2 h-2 rounded-full mr-2 ${
                    apiHealthy() === null
                      ? 'bg-gray-400'
                      : apiHealthy()
                      ? 'bg-green-400'
                      : 'bg-red-400'
                  }`}
                  title={
                    apiHealthy() === null
                      ? 'API status unknown'
                      : apiHealthy()
                      ? 'API is healthy'
                      : 'API is unavailable'
                  }
                ></div>
                <span class="text-xs text-gray-500 hidden sm:inline">
                  {apiHealthy() === null ? 'Checking' : apiHealthy() ? 'API Online' : 'API Offline'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Search Section */}
        <div class="mb-8">
          <SearchInput
            onSearch={handleSearch}
            debounceTime={debounceTime()}
            loading={searchState().loading}
          />
        </div>

        {/* Results Section */}
        <div class="flex flex-col lg:flex-row gap-8">
          {/* Sidebar with filters */}
          <aside class="lg:w-64 flex-shrink-0">
            <FilterPanel
              filters={searchState().filters}
              onFiltersChange={handleFiltersChange}
              loading={searchState().loading}
            />
          </aside>

          {/* Results */}
          <div class="flex-1 min-w-0">
            {/* Search Metadata */}
            <Show when={searchState().query && !searchState().loading}>
              <div class="flex items-center justify-between mb-6 text-sm text-gray-600">
                <div>
                  {formatResultCount(searchState().total)} for "{searchState().query}"
                </div>
                <Show when={searchTime()}>
                  <div>
                    {formatSearchTime(searchTime()!)}
                  </div>
                </Show>
              </div>
            </Show>

            {/* Loading State */}
            <Show when={searchState().loading}>
              <LoadingSpinner 
                text="Searching packages..." 
                size="lg" 
              />
            </Show>

            {/* Results */}
            <Show when={!searchState().loading}>
              <SearchResults
                results={searchState().results}
                query={searchState().query}
                error={searchState().error}
                onRetry={handleRetry}
              />
            </Show>

            {/* Pagination */}
            <Show when={searchState().total > pageSize() && !searchState().loading && !searchState().error}>
              <PaginationControls
                currentPage={searchState().currentPage}
                totalResults={searchState().total}
                pageSize={pageSize()}
                onPageChange={handlePageChange}
                loading={searchState().loading}
              />
            </Show>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer class="bg-white border-t border-gray-200 mt-16">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div class="space-y-3 text-sm text-gray-600 text-center">
            <p>
              This site provides a searchable index of the Nixpkgs package repository. It is an independent community project and is not affiliated with or endorsed by the NixOS Foundation. Package data is sourced from the Nixpkgs repository, which is licensed under the MIT License. For full license details, see our <a href="/about.html" class="text-nix-blue hover:text-nix-dark font-medium">About</a> page.
            </p>
            <p>
              <a
                href="https://github.com/james-ralph8555/fdnix/tree/main"
                target="_blank"
                rel="noopener noreferrer"
                class="text-nix-blue hover:text-nix-dark font-medium inline-flex items-center gap-2"
              >
                <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 0C5.374 0 0 5.373 0 12 0 17.302 3.438 21.8 8.207 23.387c.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/>
                </svg>
                View on GitHub
              </a>
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

export default App;
