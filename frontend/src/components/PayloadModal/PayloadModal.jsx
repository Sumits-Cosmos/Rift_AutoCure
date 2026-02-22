import { X } from 'lucide-react';

/**
 * Payload Modal — Dark glassmorphic overlay with JSON display
 */
const PayloadModal = ({ isOpen, onClose, payload, testCaseName }) => {
  if (!isOpen) return null;
  const jsonString = JSON.stringify(payload, null, 2);

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="glass-card border-white/10 max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col shadow-2xl shadow-black/40"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="bg-white/[0.05] px-6 py-4 flex items-center justify-between border-b border-white/[0.06]">
          <div>
            <h3 className="text-lg font-bold text-white">Payload Details</h3>
            <p className="text-sm text-slate-500 mt-0.5">{testCaseName}</p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors p-1 rounded-lg hover:bg-white/[0.05]"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {Object.keys(payload).length === 0 ? (
            <p className="text-slate-500 text-center py-8">No payload data for this request</p>
          ) : (
            <div className="bg-white/[0.03] rounded-xl p-4 border border-white/[0.05]">
              <pre className="text-sm font-mono text-indigo-200 whitespace-pre-wrap break-words leading-relaxed">
                {jsonString}
              </pre>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="bg-white/[0.03] px-6 py-4 border-t border-white/[0.06] flex justify-end">
          <button
            onClick={onClose}
            className="px-5 py-2 rounded-lg bg-white/[0.06] hover:bg-white/10 text-slate-300 transition-colors font-medium text-sm border border-white/10"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default PayloadModal;
