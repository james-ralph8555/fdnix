interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  text?: string;
  class?: string;
}

export function LoadingSpinner(props: LoadingSpinnerProps) {
  const { size = 'md', text, class: className = '' } = props;

  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-8 w-8',
    lg: 'h-12 w-12',
  };

  const textSizeClasses = {
    sm: 'text-sm',
    md: 'text-base',
    lg: 'text-lg',
  };

  return (
    <div class={`flex flex-col items-center justify-center p-8 ${className}`}>
      <div
        class={`animate-spin border-4 border-nix-light border-t-nix-blue rounded-full ${sizeClasses[size]}`}
        role="status"
        aria-label="Loading"
      ></div>
      {text && (
        <p class={`mt-4 text-gray-600 ${textSizeClasses[size]}`}>
          {text}
        </p>
      )}
    </div>
  );
}