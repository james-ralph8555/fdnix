import type { SearchResponse, SearchParams, ApiError } from '../types';

// Get API base URL from environment variable
const getApiBaseUrl = () => {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (!envUrl) {
    throw new Error('VITE_API_BASE_URL environment variable is required');
  }
  return envUrl;
};

const API_BASE_URL = getApiBaseUrl();

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl.replace(/\/$/, ''); // Remove trailing slash
  }

  async search(params: SearchParams, abortSignal?: AbortSignal): Promise<SearchResponse> {
    const searchParams = new URLSearchParams();
    
    // Required parameter
    searchParams.set('q', params.q);
    
    // Optional parameters
    if (params.limit !== undefined) {
      searchParams.set('limit', params.limit.toString());
    }
    if (params.offset !== undefined) {
      searchParams.set('offset', params.offset.toString());
    }
    if (params.license) {
      searchParams.set('license', params.license);
    }
    if (params.category) {
      searchParams.set('category', params.category);
    }

    const url = `${this.baseUrl}/search?${searchParams.toString()}`;
    
    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
        },
        signal: abortSignal,
      });

      if (!response.ok) {
        let errorData: ApiError;
        try {
          errorData = await response.json();
        } catch {
          errorData = {
            error: 'HTTP_ERROR',
            message: `HTTP ${response.status}: ${response.statusText}`,
            status: response.status,
          };
        }
        throw new ApiError(errorData.message, errorData.status);
      }

      const data: SearchResponse = await response.json();
      return data;
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
      
      // Handle network errors and other exceptions
      if (error instanceof TypeError && error.message.includes('fetch')) {
        throw new ApiError('Network error: Unable to connect to the API', 0);
      }
      
      throw new ApiError('An unexpected error occurred', 0);
    }
  }

  async healthCheck(): Promise<{ status: string; timestamp: string; version: string }> {
    const url = `${this.baseUrl}/health`;
    
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new ApiError(`Health check failed: ${response.statusText}`, response.status);
      }
      return await response.json();
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
      throw new ApiError('Health check failed', 0);
    }
  }

  // Update base URL (useful for testing or configuration changes)
  setBaseUrl(url: string): void {
    this.baseUrl = url.replace(/\/$/, '');
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }
}

// Custom error class for API errors
class ApiError extends Error {
  public status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// Export singleton instance
export const apiClient = new ApiClient();
export { ApiError };
export type { ApiClient };