import { For, Show, createSignal } from 'solid-js';
import type { Package } from '../types';
import { formatList, truncateText } from '../utils/format';
import { enhancePackage, getLicenseList, getInstallCommand, getShellCommand } from '../utils/package';

interface SearchResultsProps {
  results: Package[];
  query: string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function SearchResults(props: SearchResultsProps) {
  const { results, query, loading = false, error = null, onRetry } = props;

  return (
    <div class="w-full">
      <Show when={error}>
        <div class="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <div class="flex items-start justify-between">
            <div class="flex">
              <svg class="h-5 w-5 text-red-400 mr-2 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <div>
                <h3 class="text-sm font-medium text-red-800">Search Error</h3>
                <p class="mt-1 text-sm text-red-700">{error}</p>
              </div>
            </div>
            <Show when={onRetry}>
              <button
                onClick={onRetry}
                class="ml-4 px-3 py-1 bg-red-100 hover:bg-red-200 text-red-800 text-sm font-medium rounded transition-colors"
              >
                Retry
              </button>
            </Show>
          </div>
        </div>
      </Show>

      <Show when={!loading && !error && results.length === 0 && query.length > 0}>
        <div class="text-center py-12">
          <svg class="mx-auto h-16 w-16 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <h3 class="mt-4 text-lg font-medium text-gray-700">No packages found</h3>
          <p class="mt-2 text-gray-500 max-w-md mx-auto">
            We couldn't find any packages matching "{query}". Try different keywords or check your spelling.
          </p>
          <div class="mt-4 text-sm text-gray-400">
            <p>Search tips:</p>
            <ul class="mt-2 space-y-1">
              <li>• Use specific package names like "firefox" or "git"</li>
              <li>• Try broader terms like "editor" or "browser"</li>
              <li>• Check for common abbreviations or alternative names</li>
            </ul>
          </div>
        </div>
      </Show>

      <div class="grid gap-4 md:gap-6">
        <For each={results}>
          {(pkg) => <PackageCard package={pkg} />}
        </For>
      </div>
    </div>
  );
}

interface PackageCardProps {
  package: Package;
}

function PackageCard(props: PackageCardProps) {
  const { package: rawPkg } = props;
  const pkg = enhancePackage(rawPkg); // Enhance with parsed license info
  const [showCommands, setShowCommands] = createSignal(false);
  const [copiedCommand, setCopiedCommand] = createSignal<string | null>(null);

  const copyToClipboard = async (text: string, commandType: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedCommand(commandType);
      setTimeout(() => setCopiedCommand(null), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const getLicenseColor = (licenses: string[]) => {
    if (!licenses || licenses.length === 0) {
      return 'bg-gray-100 text-gray-800';
    }
    
    if (licenses.some(l => l && l.toLowerCase().includes('mit') || l && l.toLowerCase().includes('bsd'))) {
      return 'bg-green-100 text-green-800';
    }
    if (licenses.some(l => l && l.toLowerCase().includes('gpl'))) {
      return 'bg-blue-100 text-blue-800';
    }
    if (licenses.some(l => l && l.toLowerCase().includes('apache'))) {
      return 'bg-purple-100 text-purple-800';
    }
    return 'bg-gray-100 text-gray-800';
  };

  return (
    <div class="package-card">
      <div class="flex justify-between items-start mb-4">
        <div class="flex-1">
          <div class="flex items-center gap-3 mb-2">
            <h3 class="text-xl font-semibold text-gray-900">{pkg.packageName}</h3>
            <span class="text-sm text-gray-500 font-mono bg-gray-100 px-2 py-1 rounded">
              {pkg.version}
            </span>
            {pkg.broken && (
              <span class="text-xs bg-red-100 text-red-800 px-2 py-1 rounded-full font-medium">
                Broken
              </span>
            )}
            {pkg.unfree && (
              <span class="text-xs bg-orange-100 text-orange-800 px-2 py-1 rounded-full font-medium">
                Unfree
              </span>
            )}
          </div>
          
          <p class="text-gray-700 mb-3 leading-relaxed">
            {truncateText(pkg.description, 200)}
          </p>
        </div>
        
        <button
          onClick={() => setShowCommands(!showCommands())}
          class="ml-4 btn-secondary text-sm"
        >
          Install
        </button>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
        <div>
          <span class="font-medium text-gray-600">License:</span>
          <div class="mt-1 flex flex-wrap gap-1">
            <For each={getLicenseList(pkg.license).slice(0, 3)}>
              {(license) => (
                <span class={`px-2 py-1 rounded-full text-xs font-medium ${getLicenseColor([license])}`}>
                  {license}
                </span>
              )}
            </For>
            {getLicenseList(pkg.license).length > 3 && (
              <span class="px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                +{getLicenseList(pkg.license).length - 3} more
              </span>
            )}
          </div>
        </div>

        <div>
          <span class="font-medium text-gray-600">Category:</span>
          <p class="mt-1 text-gray-800">{pkg.category}</p>
        </div>

        <div>
          <span class="font-medium text-gray-600">Maintainers:</span>
          <p class="mt-1 text-gray-800" title={pkg.maintainers.join(', ')}>
            {formatList(pkg.maintainers, 2)}
          </p>
        </div>
      </div>

      {pkg.platforms.length > 0 && (
        <div class="mt-3 text-sm">
          <span class="font-medium text-gray-600">Platforms:</span>
          <div class="mt-1 flex flex-wrap gap-1">
            <For each={pkg.platforms.slice(0, 6)}>
              {(platform) => (
                <span class="px-2 py-1 rounded text-xs bg-gray-100 text-gray-700 font-mono">
                  {platform}
                </span>
              )}
            </For>
            {pkg.platforms.length > 6 && (
              <span class="px-2 py-1 rounded text-xs bg-gray-100 text-gray-700">
                +{pkg.platforms.length - 6} more
              </span>
            )}
          </div>
        </div>
      )}

      {pkg.homepage && (
        <div class="mt-3">
          <a
            href={pkg.homepage}
            target="_blank"
            rel="noopener noreferrer"
            class="text-nix-blue hover:text-nix-dark text-sm font-medium inline-flex items-center gap-1"
          >
            Visit homepage
            <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>
      )}

      <Show when={showCommands()}>
        <div class="mt-4 pt-4 border-t border-gray-200">
          <h4 class="text-sm font-medium text-gray-700 mb-3">Installation Commands</h4>
          <div class="space-y-2">
            <CommandButton
              label="Install globally"
              command={getInstallCommand(pkg)}
              onCopy={(cmd) => copyToClipboard(cmd, 'install')}
              copied={copiedCommand() === 'install'}
            />
            <CommandButton
              label="Temporary shell"
              command={getShellCommand(pkg)}
              onCopy={(cmd) => copyToClipboard(cmd, 'shell')}
              copied={copiedCommand() === 'shell'}
            />
          </div>
        </div>
      </Show>
    </div>
  );
}

interface CommandButtonProps {
  label: string;
  command: string;
  onCopy: (command: string) => void;
  copied: boolean;
}

function CommandButton(props: CommandButtonProps) {
  const { label, command, onCopy, copied } = props;

  return (
    <div class="bg-gray-50 rounded-lg p-3">
      <div class="flex items-center justify-between">
        <div class="flex-1">
          <p class="text-xs text-gray-600 mb-1">{label}</p>
          <code class="text-sm font-mono text-gray-800 bg-white px-2 py-1 rounded border">
            {command}
          </code>
        </div>
        <button
          onClick={() => onCopy(command)}
          class={`ml-3 px-3 py-1 rounded text-sm font-medium transition-colors ${
            copied
              ? 'bg-green-100 text-green-800'
              : 'bg-gray-200 hover:bg-gray-300 text-gray-700'
          }`}
        >
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
    </div>
  );
}