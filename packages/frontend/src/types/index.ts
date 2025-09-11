// Package types from the search API (matching actual API response)
export interface Package {
  attributePath: string;
  packageName: string;
  packageId: string;
  version: string;
  description: string;
  license: string; // JSON string that needs parsing
  homepage?: string;
  relevanceScore: number;
  // Additional fields we might want to parse from license or add later
  licenseList?: string[];
  category?: string;
  maintainers?: string[];
  platforms?: string[];
  broken?: boolean;
  unfree?: boolean;
}

// Search API response types (matching actual API response)
export interface SearchResponse {
  message: string;
  query: string;
  total_count: number;
  query_time_ms: number;
  search_type: string;
  packages: Package[];
  // Optional fields that may be null
  note?: string | null;
  version?: string | null;
  runtime?: string | null;
}

export interface ApiError {
  error: string;
  message: string;
  status: number;
}

// Search parameters
export interface SearchParams {
  q: string;
  limit?: number;
  offset?: number;
  license?: string;
  category?: string;
}

// Application state types
export interface SearchState {
  query: string;
  results: Package[];
  total: number;
  loading: boolean;
  error: string | null;
  currentPage: number;
  pageSize: number;
  filters: SearchFilters;
}

export interface SearchFilters {
  license: string;
  category: string;
  showBroken: boolean;
  showUnfree: boolean;
}

// Settings types
export interface AppSettings {
  debounceTime: number;
  apiBaseUrl: string;
  resultsPerPage: number;
}

// Dependency graph types
export interface NodeData {
  id: string;
  name: string;
  version?: string;
  type: 'main' | 'dependency' | 'dependent';
  category?: string;
  description?: string;
  expanded?: boolean;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface LinkData {
  source: string | NodeData;
  target: string | NodeData;
  type: 'dependency' | 'dependent';
}

export interface GraphData {
  nodes: NodeData[];
  links: LinkData[];
}

export interface DependencyGraphProps {
  packageName: string;
  packageVersion?: string;
  onClose: () => void;
}

export interface PackageNodeData {
  attributePath: string;
  available: boolean;
  broken: boolean;
  category: string;
  dependencies: {
    all: string[];
    count: number;
    direct: string[];
    totalCount: number;
  };
  dependents: {
    all: string[];
    count: number;
    direct: string[];
    totalCount: number;
  };
  description: string;
  homepage?: string;
  license: {
    licenses: Array<{
      deprecated: boolean;
      free: boolean;
      fullName: string;
      redistributable: boolean;
      shortName: string;
      spdxId: string;
      url: string;
    }>;
    type: string;
  };
  maintainers: Array<{
    email: string;
    github: string;
    githubId: number;
    name: string;
  }>;
  nodeId: string;
  packageName: string;
  platforms: string[];
  version: string;
}