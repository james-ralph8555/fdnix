interface PaginationControlsProps {
  currentPage: number;
  totalResults: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  loading?: boolean;
}

export function PaginationControls(props: PaginationControlsProps) {
  const { currentPage, totalResults, pageSize, onPageChange, loading = false } = props;

  const totalPages = Math.ceil(totalResults / pageSize);
  const startResult = (currentPage - 1) * pageSize + 1;
  const endResult = Math.min(currentPage * pageSize, totalResults);

  if (totalPages <= 1) return null;

  const getPageNumbers = () => {
    const pages: number[] = [];
    const maxVisible = 7; // Show up to 7 page numbers
    
    if (totalPages <= maxVisible) {
      // Show all pages if total is small
      for (let i = 1; i <= totalPages; i++) {
        pages.push(i);
      }
    } else {
      // Show first page
      pages.push(1);
      
      let start = Math.max(2, currentPage - 2);
      let end = Math.min(totalPages - 1, currentPage + 2);
      
      // Add ellipsis after first page if needed
      if (start > 2) {
        pages.push(-1); // -1 represents ellipsis
        start = Math.max(start, currentPage - 1);
      }
      
      // Add middle pages
      for (let i = start; i <= end; i++) {
        pages.push(i);
      }
      
      // Add ellipsis before last page if needed
      if (end < totalPages - 1) {
        pages.push(-1); // -1 represents ellipsis
      }
      
      // Show last page
      pages.push(totalPages);
    }
    
    return pages;
  };

  const pageNumbers = getPageNumbers();

  const handlePageClick = (page: number) => {
    if (page !== currentPage && !loading) {
      onPageChange(page);
    }
  };

  const handlePrevious = () => {
    if (currentPage > 1 && !loading) {
      onPageChange(currentPage - 1);
    }
  };

  const handleNext = () => {
    if (currentPage < totalPages && !loading) {
      onPageChange(currentPage + 1);
    }
  };

  return (
    <div class="flex flex-col sm:flex-row items-center justify-between gap-4 mt-8">
      {/* Results info */}
      <div class="text-sm text-gray-600">
        Showing {startResult.toLocaleString()} to {endResult.toLocaleString()} of{' '}
        {totalResults.toLocaleString()} results
      </div>

      {/* Page controls */}
      <div class="flex items-center gap-1">
        {/* Previous button */}
        <button
          onClick={handlePrevious}
          disabled={currentPage <= 1 || loading}
          class={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
            currentPage <= 1 || loading
              ? 'text-gray-400 cursor-not-allowed'
              : 'text-gray-700 hover:bg-gray-100'
          }`}
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        {/* Page numbers */}
        {pageNumbers.map((page, index) => {
          if (page === -1) {
            return (
              <span key={`ellipsis-${index}`} class="px-3 py-2 text-gray-400">
                ...
              </span>
            );
          }

          return (
            <button
              key={page}
              onClick={() => handlePageClick(page)}
              disabled={loading}
              class={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                page === currentPage
                  ? 'bg-nix-blue text-white'
                  : loading
                  ? 'text-gray-400 cursor-not-allowed'
                  : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              {page}
            </button>
          );
        })}

        {/* Next button */}
        <button
          onClick={handleNext}
          disabled={currentPage >= totalPages || loading}
          class={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
            currentPage >= totalPages || loading
              ? 'text-gray-400 cursor-not-allowed'
              : 'text-gray-700 hover:bg-gray-100'
          }`}
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  );
}