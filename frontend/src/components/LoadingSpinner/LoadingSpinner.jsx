/**
 * Loading Spinner — Gradient ring with glow
 */
const LoadingSpinner = () => (
  <div className="flex flex-col items-center py-6">
    <div className="relative w-12 h-12 mb-4">
      <div className="absolute inset-0 rounded-full border-2 border-white/[0.05]" />
      <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-indigo-500 border-r-purple-500 animate-spin" />
      <div className="absolute inset-1 rounded-full bg-indigo-500/10 animate-pulse" />
    </div>
    <p className="text-slate-400 font-medium text-sm">Processing...</p>
  </div>
);

export default LoadingSpinner;
