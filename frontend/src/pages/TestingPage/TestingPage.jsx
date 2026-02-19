import { useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import FileUpload from '../../components/FileUpload/FileUpload';
import TestTable from '../../components/TestTable/TestTable';
import TestResults from '../../components/TestResults/TestResults';
import LoadingSpinner from '../../components/LoadingSpinner/LoadingSpinner';
import { generateTestCases, generateTestCasesFromSpec, getCollection, updateTestCases, executeBatch } from '../../services/api';

const BATCH_SIZE = 10;

/**
 * Testing Page — Dark themed testing interface
 */
const TestingPage = ({ onBack }) => {
  const [swaggerUrl, setSwaggerUrl] = useState('');
  const [testCases, setTestCases] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [uploadedSpec, setUploadedSpec] = useState(null);
  const [runId, setRunId] = useState(null);
  const [error, setError] = useState(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [batchProgress, setBatchProgress] = useState(null);

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      setUploadedFile(file);
      setError(null);
      if (file.name.endsWith('.json')) {
        const reader = new FileReader();
        reader.onload = (event) => {
          try {
            const content = JSON.parse(event.target.result);
            setUploadedSpec(content);
            let extractedBaseUrl = '';
            if (content.servers && content.servers[0]?.url) {
              extractedBaseUrl = content.servers[0].url;
            } else if (content.host) {
              const scheme = content.schemes?.[0] || 'https';
              extractedBaseUrl = `${scheme}://${content.host}${content.basePath || ''}`;
            }
            setBaseUrl(extractedBaseUrl);
          } catch (err) {
            setError('Could not parse file as JSON. Please upload a valid Swagger/OpenAPI JSON file.');
            setUploadedSpec(null);
          }
        };
        reader.readAsText(file);
      }
    }
  };

  const fetchTestCases = async () => {
    if (!uploadedSpec && !swaggerUrl.trim()) {
      setError('Please upload a Swagger JSON file or enter a Swagger URL');
      return;
    }
    setIsLoading(true);
    setError(null);
    setResults(null);
    setTestCases([]);
    try {
      let specToSend = uploadedSpec;
      if (uploadedSpec && baseUrl) {
        specToSend = { ...uploadedSpec, servers: [{ url: baseUrl }] };
      }
      const response = uploadedSpec
        ? await generateTestCasesFromSpec(specToSend)
        : await generateTestCases(swaggerUrl);
      setRunId(response.runId);
      const transformedTestCases = response.testcases.map((tc, index) => ({
        id: index + 1,
        method: tc.method,
        endpoint: tc.path,
        expected: tc.expected,
        description: tc.description || tc.name,
        category: tc.category,
        priority: tc.priority,
        payloadData: tc.payloadData || {},
        selected: true
      }));
      setTestCases(transformedTestCases);
    } catch (err) {
      setError(err.message || 'Failed to generate test cases');
    } finally {
      setIsLoading(false);
    }
  };

  const toggleSelection = (id) => {
    setTestCases(prev => prev.map(tc => (tc.id === id ? { ...tc, selected: !tc.selected } : tc)));
  };

  const deleteTestCase = (id) => {
    setTestCases(prev => prev.filter(tc => tc.id !== id));
  };

  const runTests = async () => {
    if (!runId || testCases.length === 0) return;
    setIsRunning(true);
    setResults(null);
    setError(null);
    setBatchProgress(null);
    let aggregatedResults = { total: 0, passed: 0, failed: 0, failedEndpoints: [], successEndpoints: [] };
    try {
      const { collection } = await getCollection(runId);
      const deletedIndices = new Set();
      const maxId = Math.max(...testCases.map(tc => tc.id), 0);
      for (let i = 1; i <= maxId; i++) {
        if (!testCases.find(tc => tc.id === i)) deletedIndices.add(i - 1);
      }
      const filteredItems = collection.item.filter((item, index) => !deletedIndices.has(index));
      const filteredCollection = { ...collection, item: filteredItems };
      await updateTestCases(runId, filteredCollection);
      const totalTests = testCases.length;
      const totalBatches = Math.ceil(totalTests / BATCH_SIZE);
      for (let batchIndex = 0; batchIndex < totalBatches; batchIndex++) {
        setBatchProgress({ currentBatch: batchIndex + 1, totalBatches, testedSoFar: batchIndex * BATCH_SIZE, totalTests });
        const batchResult = await executeBatch(runId, batchIndex, BATCH_SIZE);
        aggregatedResults.total += batchResult.summary.total || 0;
        aggregatedResults.passed += batchResult.summary.passed || 0;
        aggregatedResults.failed += batchResult.summary.failed || 0;
        aggregatedResults.failedEndpoints = [...aggregatedResults.failedEndpoints, ...(batchResult.summary.failedEndpoints || [])];
        aggregatedResults.successEndpoints = [...aggregatedResults.successEndpoints, ...(batchResult.summary.successEndpoints || [])];
        setResults({ ...aggregatedResults });
        setBatchProgress({ currentBatch: batchIndex + 1, totalBatches, testedSoFar: Math.min((batchIndex + 1) * BATCH_SIZE, totalTests), totalTests });
        if (batchResult.isComplete) break;
      }
      setResults(aggregatedResults);
      setBatchProgress(null);
    } catch (err) {
      setError(err.message || 'Failed to execute tests');
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto py-8 px-6 space-y-6 w-full animate-fadeInUp">
      {/* Error */}
      {error && (
        <div className="bg-rose-500/10 border border-rose-500/20 text-rose-300 px-4 py-3 rounded-xl text-sm flex items-center gap-2">
          <span className="text-rose-400">⚠</span> {error}
        </div>
      )}

      {/* Upload Section */}
      <FileUpload uploadedFile={uploadedFile} onFileChange={handleFileChange} />

      {/* Divider */}
      <div className="flex items-center gap-4">
        <div className="flex-1 border-t border-white/[0.06]"></div>
        <span className="text-slate-600 font-semibold text-sm">OR</span>
        <div className="flex-1 border-t border-white/[0.06]"></div>
      </div>

      {/* Input / Fetch Row */}
      <div className="flex gap-3">
        <input
          type="text"
          placeholder="Enter Swagger URL"
          className={`input-dark flex-1 px-4 py-3 ${uploadedSpec ? 'opacity-50' : ''}`}
          value={swaggerUrl}
          onChange={(e) => setSwaggerUrl(e.target.value)}
          disabled={!!uploadedSpec}
        />
        <button
          onClick={fetchTestCases}
          disabled={isLoading || (!uploadedSpec && !swaggerUrl.trim())}
          className="btn-primary px-6 py-3 whitespace-nowrap text-sm"
        >
          {isLoading ? 'Generating...' : 'Generate Test Cases'}
        </button>
      </div>

      {/* Uploaded file info */}
      {uploadedSpec && (
        <div className="glass-card p-4 space-y-3">
          <p className="text-sm text-emerald-300 flex items-center gap-2">
            ✓ Using uploaded file: <strong className="text-white">{uploadedFile?.name}</strong>
            <button
              onClick={() => { setUploadedFile(null); setUploadedSpec(null); setSwaggerUrl(''); setBaseUrl(''); }}
              className="ml-2 text-rose-400 hover:text-rose-300 text-xs underline"
            >
              Clear
            </button>
          </p>
          <div>
            <label className="block text-sm font-medium text-slate-400 mb-1.5">API Base URL (for running tests):</label>
            <input
              type="text"
              placeholder="e.g., https://petstore.swagger.io/v2"
              className="input-dark w-full px-4 py-2.5"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
            {baseUrl && <p className="text-xs text-slate-500 mt-1">Tests will run against: {baseUrl}</p>}
          </div>
        </div>
      )}

      {/* Loading */}
      {isLoading && <LoadingSpinner />}

      {/* Table */}
      <TestTable testCases={testCases} onToggleSelection={toggleSelection} onDeleteTestCase={deleteTestCase} />

      {/* Execution Progress */}
      {isRunning && (
        <div className="glass-card p-6">
          <LoadingSpinner />
          {batchProgress && (
            <div className="mt-4 text-center">
              <p className="text-lg font-bold text-white mb-2">
                Running Batch {batchProgress.currentBatch} of {batchProgress.totalBatches}
              </p>
              <div className="w-full bg-white/[0.05] rounded-full h-2.5 mb-2 overflow-hidden">
                <div
                  className="bg-gradient-to-r from-indigo-500 to-purple-500 h-2.5 rounded-full transition-all duration-300"
                  style={{ width: `${(batchProgress.testedSoFar / batchProgress.totalTests) * 100}%` }}
                ></div>
              </div>
              <p className="text-sm text-slate-400">
                {batchProgress.testedSoFar} of {batchProgress.totalTests} tests completed
              </p>
            </div>
          )}
        </div>
      )}

      {results && <TestResults results={results} isPartial={isRunning} />}

      {/* Footer Buttons */}
      <div className="flex items-center justify-between pt-6 pb-8">
        <button
          onClick={onBack}
          className="flex items-center gap-2 px-6 py-3 bg-white/[0.05] border border-white/10 text-slate-300 rounded-xl hover:bg-white/10 transition-all text-sm font-medium"
        >
          <ArrowLeft className="w-4 h-4" /> Back
        </button>
        {testCases.length > 0 && (
          <button
            onClick={runTests}
            disabled={isRunning}
            className="btn-primary px-10 py-3 text-sm"
          >
            Run Tests
          </button>
        )}
      </div>
    </div>
  );
};

export default TestingPage;
