import { createSignal, For, Show } from 'solid-js';
import type { SearchFilters } from '../types';

interface FilterPanelProps {
  filters: SearchFilters;
  onFiltersChange: (filters: SearchFilters) => void;
  loading?: boolean;
}

// Common license types in nixpkgs
const COMMON_LICENSES = [
  { value: '', label: 'All Licenses' },
  { value: 'mit', label: 'MIT' },
  { value: 'bsd', label: 'BSD' },
  { value: 'gpl', label: 'GPL' },
  { value: 'apache', label: 'Apache' },
  { value: 'lgpl', label: 'LGPL' },
  { value: 'mpl', label: 'MPL' },
];

// Common package categories
const COMMON_CATEGORIES = [
  { value: '', label: 'All Categories' },
  { value: 'applications', label: 'Applications' },
  { value: 'development', label: 'Development' },
  { value: 'games', label: 'Games' },
  { value: 'servers', label: 'Servers' },
  { value: 'tools', label: 'Tools' },
  { value: 'libraries', label: 'Libraries' },
  { value: 'desktops', label: 'Desktops' },
  { value: 'misc', label: 'Miscellaneous' },
];

export function FilterPanel(props: FilterPanelProps) {
  const { filters, onFiltersChange, loading = false } = props;
  const [isExpanded, setIsExpanded] = createSignal(false);

  const updateFilter = <K extends keyof SearchFilters>(
    key: K,
    value: SearchFilters[K]
  ) => {
    onFiltersChange({ ...filters, [key]: value });
  };

  const clearAllFilters = () => {
    onFiltersChange({
      license: '',
      category: '',
      showBroken: false,
      showUnfree: false,
    });
  };

  const hasActiveFilters = () => {
    return filters.license || filters.category || filters.showBroken || filters.showUnfree;
  };

  return (
    <div class="filter-panel">
      {/* Mobile Toggle */}
      <div class="md:hidden">
        <button
          onClick={() => setIsExpanded(!isExpanded())}
          class="w-full flex items-center justify-between p-3 text-left"
        >
          <span class="font-medium text-gray-700">
            Filters {hasActiveFilters() && `(${Object.values(filters).filter(Boolean).length} active)`}
          </span>
          <svg
            class={`h-5 w-5 text-gray-500 transition-transform ${
              isExpanded() ? 'rotate-180' : ''
            }`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      {/* Filter Content */}
      <div class={`${isExpanded() ? 'block' : 'hidden'} md:block`}>
        <div class="hidden md:flex md:items-center md:justify-between mb-4">
          <h3 class="font-semibold text-gray-800">Filters</h3>
          <Show when={hasActiveFilters()}>
            <button
              onClick={clearAllFilters}
              disabled={loading}
              class="text-sm text-nix-blue hover:text-nix-dark disabled:opacity-50"
            >
              Clear all
            </button>
          </Show>
        </div>

        <div class="space-y-4">
          {/* License Filter */}
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">
              License
            </label>
            <select
              value={filters.license}
              onChange={(e) => updateFilter('license', e.target.value)}
              disabled={loading}
              class="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-nix-blue focus:ring-2 focus:ring-nix-light/20 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <For each={COMMON_LICENSES}>
                {(license) => (
                  <option value={license.value}>{license.label}</option>
                )}
              </For>
            </select>
          </div>

          {/* Category Filter */}
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">
              Category
            </label>
            <select
              value={filters.category}
              onChange={(e) => updateFilter('category', e.target.value)}
              disabled={loading}
              class="w-full px-3 py-2 border border-gray-200 rounded-lg focus:border-nix-blue focus:ring-2 focus:ring-nix-light/20 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <For each={COMMON_CATEGORIES}>
                {(category) => (
                  <option value={category.value}>{category.label}</option>
                )}
              </For>
            </select>
          </div>

          {/* Package Status Filters */}
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-3">
              Package Status
            </label>
            <div class="space-y-2">
              <label class="flex items-center">
                <input
                  type="checkbox"
                  checked={filters.showBroken}
                  onChange={(e) => updateFilter('showBroken', e.target.checked)}
                  disabled={loading}
                  class="h-4 w-4 text-nix-blue border-gray-300 rounded focus:ring-nix-light focus:ring-2 disabled:opacity-50"
                />
                <span class="ml-3 text-sm text-gray-700">
                  Include broken packages
                </span>
              </label>
              
              <label class="flex items-center">
                <input
                  type="checkbox"
                  checked={filters.showUnfree}
                  onChange={(e) => updateFilter('showUnfree', e.target.checked)}
                  disabled={loading}
                  class="h-4 w-4 text-nix-blue border-gray-300 rounded focus:ring-nix-light focus:ring-2 disabled:opacity-50"
                />
                <span class="ml-3 text-sm text-gray-700">
                  Include unfree packages
                </span>
              </label>
            </div>
          </div>

          {/* Mobile Clear Button */}
          <div class="md:hidden pt-4 border-t border-gray-200">
            <Show when={hasActiveFilters()}>
              <button
                onClick={clearAllFilters}
                disabled={loading}
                class="w-full btn-secondary disabled:opacity-50"
              >
                Clear all filters
              </button>
            </Show>
          </div>
        </div>
      </div>
    </div>
  );
}