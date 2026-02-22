import { CheckCircle2, XCircle, Clock, Loader2 } from 'lucide-react';
import { useState } from 'react';

/**
 * Test Results Summary — Dark themed with colored stat cards
 */
const TestResults = ({ results, isPartial = false }) => {
  const [activeTab, setActiveTab] = useState('failed');
  if (!results) return null;

  return (
    <div className="glass-card p-6 animate-fadeInUp">
      <h3 className="text-xl font-bold mb-5 flex items-center gap-2 text-white">
        {isPartial ? (
          <>
            <Loader2 className="text-indigo-400 animate-spin" size={20} />
            Test Results <span className="text-slate-500 text-sm font-normal">(In Progress...)</span>
          </>
        ) : (
          <>
            <CheckCircle2 className="text-emerald-400" size={20} />
            Test Summary
          </>
        )}
      </h3>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        <div className="bg-white/[0.04] rounded-xl p-4 text-center border border-white/[0.05]">
          <p className="text-[10px] text-slate-500 uppercase font-bold tracking-wider mb-1">Total</p>
          <p className="text-2xl font-black text-white">{results.total}</p>
        </div>
        <div className="bg-emerald-500/[0.07] rounded-xl p-4 text-center border border-emerald-500/20">
          <p className="text-[10px] text-emerald-400 uppercase font-bold tracking-wider mb-1">Passed</p>
          <p className="text-2xl font-black text-emerald-400">{results.passed}</p>
        </div>
        <div className="bg-rose-500/[0.07] rounded-xl p-4 text-center border border-rose-500/20">
          <p className="text-[10px] text-rose-400 uppercase font-bold tracking-wider mb-1">Failed</p>
          <p className="text-2xl font-black text-rose-400">{results.failed}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 bg-white/[0.03] rounded-lg p-1">
        <button
          onClick={() => setActiveTab('failed')}
          className={`flex-1 px-4 py-2 rounded-md text-sm font-medium transition-all ${activeTab === 'failed'
              ? 'bg-rose-500/15 text-rose-300 border border-rose-500/25'
              : 'text-slate-500 hover:text-slate-300'
            }`}
        >
          Failed ({results.failedEndpoints?.length || 0})
        </button>
        <button
          onClick={() => setActiveTab('passed')}
          className={`flex-1 px-4 py-2 rounded-md text-sm font-medium transition-all ${activeTab === 'passed'
              ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/25'
              : 'text-slate-500 hover:text-slate-300'
            }`}
        >
          Passed ({results.successEndpoints?.length || 0})
        </button>
      </div>

      {/* Failed */}
      {activeTab === 'failed' && results.failedEndpoints?.length > 0 && (
        <div className="max-h-64 overflow-y-auto space-y-2">
          {results.failedEndpoints.map((ep, i) => (
            <div key={i} className="text-sm bg-rose-500/[0.06] border border-rose-500/10 px-4 py-2.5 rounded-lg flex items-start gap-2">
              <XCircle className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" />
              <div>
                <span className="font-medium text-slate-200">{typeof ep === 'string' ? ep : ep.endpoint}</span>
                {ep.message && <p className="text-xs text-rose-400/80 mt-0.5">{ep.message}</p>}
              </div>
            </div>
          ))}
        </div>
      )}
      {activeTab === 'failed' && (!results.failedEndpoints || results.failedEndpoints.length === 0) && (
        <p className="text-sm text-emerald-400 text-center py-6">🎉 All tests passed!</p>
      )}

      {/* Passed */}
      {activeTab === 'passed' && results.successEndpoints?.length > 0 && (
        <div className="max-h-64 overflow-y-auto space-y-2">
          {results.successEndpoints.map((ep, i) => (
            <div key={i} className="text-sm bg-emerald-500/[0.06] border border-emerald-500/10 px-4 py-2.5 rounded-lg flex items-center justify-between">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                <span className="font-medium text-slate-200">{typeof ep === 'string' ? ep : ep.endpoint}</span>
              </div>
              {ep.status && (
                <div className="flex items-center gap-3 text-xs text-slate-500">
                  <span className="bg-emerald-500/15 text-emerald-300 px-2 py-0.5 rounded-full border border-emerald-500/25">{ep.status}</span>
                  {ep.responseTime && (
                    <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{ep.responseTime}</span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {activeTab === 'passed' && (!results.successEndpoints || results.successEndpoints.length === 0) && (
        <p className="text-sm text-slate-500 text-center py-6">No passed tests</p>
      )}
    </div>
  );
};

export default TestResults;
