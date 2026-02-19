import { useState } from 'react';
import { Trash2, Eye } from 'lucide-react';
import PayloadModal from '../PayloadModal/PayloadModal';

/**
 * Test Cases Table — Dark themed with modern row styling
 */
const TestTable = ({ testCases, onToggleSelection, onDeleteTestCase }) => {
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedPayload, setSelectedPayload] = useState({});
  const [selectedTestCase, setSelectedTestCase] = useState('');

  const handleViewPayload = (testCase) => {
    setSelectedPayload(testCase.payloadData || {});
    setSelectedTestCase(testCase.description || testCase.method);
    setModalOpen(true);
  };

  if (testCases.length === 0) return null;

  return (
    <div className="glass-card overflow-hidden animate-fadeIn">
      <div
        className="overflow-y-auto"
        style={{ maxHeight: '400px', scrollbarGutter: 'stable' }}
      >
        <table className="w-full text-left border-collapse">
          <thead className="bg-white/[0.05] sticky top-0 z-10 border-b border-white/[0.06]">
            <tr>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider w-12 text-center">✔</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Method</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Endpoint</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Expected</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Payload</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Description</th>
              <th className="px-4 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider w-16 text-center">×</th>
            </tr>
          </thead>
          <tbody>
            {testCases.map((tc) => (
              <tr key={tc.id} className="border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors">
                <td className="px-4 py-3.5 text-center">
                  <input
                    type="checkbox"
                    checked={tc.selected}
                    onChange={() => onToggleSelection(tc.id)}
                    className="w-4 h-4 accent-indigo-500 rounded"
                  />
                </td>
                <td className="px-4 py-3.5 font-mono text-xs text-slate-400 uppercase">{tc.method}</td>
                <td className="px-4 py-3.5 text-sm text-indigo-300 font-medium">{tc.endpoint}</td>
                <td className="px-4 py-3.5 text-sm text-slate-500">{tc.expected}</td>
                <td className="px-4 py-3.5 text-sm">
                  {tc.payloadData && Object.keys(tc.payloadData).length > 0 ? (
                    <button
                      onClick={() => handleViewPayload(tc)}
                      className="flex items-center gap-1.5 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 px-3 py-1.5 rounded-lg transition-colors text-xs font-medium border border-indigo-500/20"
                    >
                      <Eye className="w-3.5 h-3.5" /> View
                    </button>
                  ) : (
                    <span className="text-slate-600 text-xs italic">None</span>
                  )}
                </td>
                <td className="px-4 py-3.5 text-sm text-slate-400 italic text-xs">{tc.description}</td>
                <td className="px-4 py-3.5 text-center">
                  <button
                    onClick={() => onDeleteTestCase(tc.id)}
                    className="text-slate-600 hover:text-rose-400 transition-colors p-1"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PayloadModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        payload={selectedPayload}
        testCaseName={selectedTestCase}
      />
    </div>
  );
};

export default TestTable;
