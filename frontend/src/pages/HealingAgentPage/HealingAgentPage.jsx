import { useState, useEffect, useRef } from 'react';
import {
    GitBranch, Play, Clock, CheckCircle2, XCircle, AlertCircle,
    Loader2, ChevronRight, Bug, Wrench, Timer, Trophy, RotateCcw,
    Code2, Terminal, Activity, Users, User
} from 'lucide-react';
import { startHealingAgent, getAgentStatus } from '../../services/healingService';

// ─── Status helpers ───────────────────────────────────────────────────────────
const STATUS_CONFIG = {
    QUEUED: { icon: Clock, color: 'text-yellow-400', bg: 'bg-yellow-400/10', label: 'Queued' },
    RUNNING: { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-400/10', label: 'Running', spin: true },
    PASSED: { icon: CheckCircle2, color: 'text-emerald-400', bg: 'bg-emerald-400/10', label: 'Passed' },
    PARTIAL: { icon: AlertCircle, color: 'text-amber-400', bg: 'bg-amber-400/10', label: 'Partial' },
    FAILED: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-400/10', label: 'Failed' },
    ERROR: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: 'Error' },
    COMPLETE: { icon: CheckCircle2, color: 'text-emerald-400', bg: 'bg-emerald-400/10', label: 'Complete' },
};

const BUG_TYPE_COLORS = {
    SYNTAX: 'bg-red-500/20 text-red-300 border-red-500/30',
    LOGIC: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    TYPE_ERROR: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
    IMPORT: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
    LINTING: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
    INDENTATION: 'bg-teal-500/20 text-teal-300 border-teal-500/30',
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }) {
    const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.QUEUED;
    const Icon = cfg.icon;
    return (
        <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border ${cfg.bg} ${cfg.color} border-current/20`}>
            <Icon size={13} className={cfg.spin ? 'animate-spin' : ''} />
            {cfg.label}
        </span>
    );
}

function CIStatusBadge({ status }) {
    const isPassed = status === 'PASSED';
    return (
        <div className={`inline-flex items-center gap-2 px-6 py-3 rounded-xl text-lg font-black ${isPassed
                ? 'bg-emerald-500/20 text-emerald-300 border-2 border-emerald-500/40'
                : 'bg-red-500/20 text-red-300 border-2 border-red-500/40'
            }`}>
            {isPassed ? <CheckCircle2 size={22} /> : <XCircle size={22} />}
            {isPassed ? 'PASSED' : 'FAILED'}
        </div>
    );
}

function RunSummaryCard({ job, result }) {
    if (!job) return null;
    const duration = result?.time_taken_seconds;
    const formatTime = (s) => {
        if (!s) return '—';
        const mins = Math.floor(s / 60);
        const secs = Math.floor(s % 60);
        return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    };

    return (
        <div className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm p-6 space-y-4">
            <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-white flex items-center gap-2">
                    <Activity size={18} className="text-indigo-400" /> Run Summary
                </h3>
                {result?.ci_status && <CIStatusBadge status={result.ci_status} />}
                {!result?.ci_status && <StatusBadge status={job.status} />}
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
                <InfoRow label="Repository" value={result?.repo_url || job.repo_url} truncate />
                <InfoRow label="Branch" value={result?.branch_name || '—'} mono />
                <InfoRow label="Team Name" value={result?.team_name || job.team_name || '—'} icon={<Users size={12} className="text-indigo-400" />} />
                <InfoRow label="Leader Name" value={result?.leader_name || job.leader_name || '—'} icon={<User size={12} className="text-indigo-400" />} />
                <InfoRow label="Language" value={result?.language || '—'} />
                <InfoRow label="Framework" value={result?.test_framework || '—'} />
                <InfoRow label="Failures detected" value={result?.total_failures ?? '—'} highlight={result?.total_failures > 0 ? 'red' : null} />
                <InfoRow label="Fixes applied" value={result?.total_fixes ?? '—'} highlight={result?.total_fixes > 0 ? 'green' : null} />
                <InfoRow label="Total commits" value={result?.total_commits ?? '—'} />
                <InfoRow label="Iterations used" value={result ? `${result.iterations_used}/${result.max_retries || 5}` : '—'} />
                <div className="col-span-2">
                    <InfoRow label="Time taken" value={formatTime(duration)} />
                </div>
            </div>
        </div>
    );
}

function InfoRow({ label, value, mono, truncate, icon, highlight }) {
    const highlightClass = highlight === 'red'
        ? 'text-red-300'
        : highlight === 'green'
            ? 'text-emerald-300'
            : 'text-white';
    return (
        <div className="bg-white/5 rounded-xl p-3 min-w-0">
            <p className="text-xs text-slate-400 mb-0.5 flex items-center gap-1">{icon}{label}</p>
            <p className={`text-sm font-semibold ${highlightClass} ${mono ? 'font-mono' : ''} ${truncate ? 'truncate' : ''}`}>
                {String(value)}
            </p>
        </div>
    );
}

function FixesTable({ fixes }) {
    if (!fixes || fixes.length === 0) {
        return (
            <div className="rounded-2xl border border-white/10 bg-white/5 p-6 text-center text-slate-400">
                <Wrench size={32} className="mx-auto mb-2 opacity-30" />
                <p className="text-sm">No fixes applied yet.</p>
            </div>
        );
    }
    return (
        <div className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-white/10 flex items-center gap-2">
                <Wrench size={18} className="text-indigo-400" />
                <h3 className="text-lg font-bold text-white">Applied Fixes</h3>
                <span className="ml-auto text-xs bg-indigo-500/20 text-indigo-300 px-2 py-0.5 rounded-full border border-indigo-500/30">
                    {fixes.length} fix{fixes.length !== 1 ? 'es' : ''}
                </span>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-white/10 text-slate-400 text-xs uppercase tracking-wider">
                            <th className="px-4 py-3 text-left">File</th>
                            <th className="px-4 py-3 text-left">Bug Type</th>
                            <th className="px-4 py-3 text-left">Line #</th>
                            <th className="px-4 py-3 text-left">Commit Message</th>
                            <th className="px-4 py-3 text-left">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {fixes.map((fix, i) => {
                            const isFixed = fix.status === 'FIXED' || fix.status === 'Fixed';
                            return (
                                <tr key={i} className={`border-b border-white/5 transition-colors ${isFixed ? 'hover:bg-emerald-500/5' : 'hover:bg-red-500/5'
                                    }`}>
                                    <td className="px-4 py-3 font-mono text-slate-200 text-xs">{fix.file}</td>
                                    <td className="px-4 py-3">
                                        <span className={`text-xs px-2 py-0.5 rounded-full border font-semibold ${BUG_TYPE_COLORS[fix.bug_type] || 'bg-slate-500/20 text-slate-300 border-slate-500/30'}`}>
                                            {fix.bug_type}
                                        </span>
                                    </td>
                                    <td className="px-4 py-3 text-slate-300 font-mono">{fix.line_number ?? fix.line}</td>
                                    <td className="px-4 py-3 text-slate-300 text-xs max-w-xs truncate" title={fix.commit_message}>
                                        {fix.commit_message}
                                    </td>
                                    <td className="px-4 py-3">
                                        <span className={`text-xs px-2 py-0.5 rounded-full font-semibold border ${isFixed
                                                ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                                                : 'bg-red-500/20 text-red-300 border-red-500/30'
                                            }`}>
                                            {isFixed ? '✓ Fixed' : '✗ Failed'}
                                        </span>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            {/* Output Lines — exact format judges check */}
            <div className="px-6 py-4 border-t border-white/10 space-y-1">
                <p className="text-xs text-slate-400 mb-2 font-medium">Test Case Output (exact format):</p>
                {fixes.map((fix, i) => (
                    fix.output_line && (
                        <div key={i} className="font-mono text-xs text-indigo-300 bg-indigo-500/5 rounded-lg px-3 py-2 border border-indigo-500/10">
                            {fix.output_line}
                        </div>
                    )
                ))}
            </div>
        </div>
    );
}

function CITimeline({ timeline, maxRetries = 5 }) {
    if (!timeline || timeline.length === 0) return null;
    return (
        <div className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm p-6">
            <div className="flex items-center gap-2 mb-1">
                <Timer size={18} className="text-indigo-400" />
                <h3 className="text-lg font-bold text-white">CI/CD Status Timeline</h3>
            </div>
            <p className="text-xs text-slate-400 mb-4">
                {timeline.length}/{maxRetries} retries used
            </p>
            <div className="space-y-3">
                {timeline.map((entry, i) => {
                    const isPassed = entry.status === 'PASSED';
                    return (
                        <div key={i} className="flex items-center gap-3">
                            {/* Circle node */}
                            <div className={`flex items-center justify-center rounded-full text-xs font-bold w-10 h-10 border-2 shrink-0 transition-all duration-300 ${isPassed
                                    ? 'bg-emerald-500/20 border-emerald-500 text-emerald-300'
                                    : 'bg-red-500/20 border-red-500 text-red-300'
                                }`}>
                                {isPassed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                            </div>
                            {/* Details */}
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                    <span className="text-sm font-semibold text-white">Run #{entry.iteration}</span>
                                    <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${isPassed
                                            ? 'bg-emerald-500/20 text-emerald-300'
                                            : 'bg-red-500/20 text-red-300'
                                        }`}>
                                        {entry.status}
                                    </span>
                                    {entry.failures_count > 0 && (
                                        <span className="text-xs text-slate-400">
                                            {entry.failures_count} failure{entry.failures_count !== 1 ? 's' : ''}
                                        </span>
                                    )}
                                </div>
                                {entry.timestamp && (
                                    <p className="text-xs text-slate-500 mt-0.5">
                                        {new Date(entry.timestamp).toLocaleTimeString()}
                                    </p>
                                )}
                            </div>
                            {/* Connector */}
                            {i < timeline.length - 1 && (
                                <div className="absolute left-5 mt-10 w-0.5 h-3 bg-white/10" />
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function ScoreBoard({ result }) {
    if (!result?.score) return null;
    const score = result.score;

    return (
        <div className="rounded-2xl border border-indigo-500/30 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 backdrop-blur-sm p-6">
            <div className="flex items-center gap-2 mb-4">
                <Trophy size={18} className="text-yellow-400" />
                <h3 className="text-lg font-bold text-white">Score Breakdown</h3>
            </div>
            <div className="flex items-center justify-between mb-4">
                <span className="text-slate-300 text-sm">Final Score</span>
                <span className={`text-4xl font-black ${score.final >= 100 ? 'text-emerald-400' : score.final >= 80 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {score.final}
                </span>
            </div>
            {/* Score bar visualization */}
            <div className="mb-4">
                <div className="w-full bg-white/10 rounded-full h-3 overflow-hidden">
                    <div
                        className={`h-full rounded-full transition-all duration-1000 ${score.final >= 100 ? 'bg-gradient-to-r from-emerald-500 to-emerald-400'
                                : score.final >= 80 ? 'bg-gradient-to-r from-yellow-500 to-yellow-400'
                                    : 'bg-gradient-to-r from-red-500 to-red-400'
                            }`}
                        style={{ width: `${Math.min(100, (score.final / 110) * 100)}%` }}
                    />
                </div>
            </div>
            <div className="space-y-2 text-sm">
                <ScoreLine label="Base Score" value="+100" color="text-slate-300" />
                <ScoreLine
                    label={`Speed Bonus (${Math.floor((score.time_taken_seconds || 0) / 60)}m ${Math.floor((score.time_taken_seconds || 0) % 60)}s)`}
                    value={score.speed_bonus > 0 ? `+${score.speed_bonus}` : '0'}
                    color={score.speed_bonus > 0 ? 'text-emerald-400' : 'text-slate-500'}
                />
                <ScoreLine
                    label={`Commit Penalty (${result.total_commits || 0} commits)`}
                    value={score.efficiency_penalty > 0 ? `-${score.efficiency_penalty}` : '0'}
                    color={score.efficiency_penalty > 0 ? 'text-red-400' : 'text-slate-500'}
                />
                <div className="border-t border-white/10 pt-2 mt-2">
                    <div className="flex justify-between items-center">
                        <span className="text-slate-300 font-medium">
                            100 (base) + {score.speed_bonus} (speed) - {score.efficiency_penalty} (penalty)
                        </span>
                        <span className="font-bold text-white">= {score.final}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function ScoreLine({ label, value, color }) {
    return (
        <div className="flex justify-between items-center py-1 border-b border-white/5">
            <span className="text-slate-400">{label}</span>
            <span className={`font-bold ${color}`}>{value}</span>
        </div>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function HealingAgentPage() {
    const [form, setForm] = useState({ repo_url: '', team_name: '', leader_name: '', retry_limit: 5 });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [job, setJob] = useState(null);
    const [result, setResult] = useState(null);
    const pollRef = useRef(null);

    // Start polling when we have a job
    useEffect(() => {
        if (!job?.job_id) return;
        if (['PASSED', 'FAILED', 'PARTIAL', 'ERROR', 'COMPLETE'].includes(job.status)) return;

        pollRef.current = setInterval(async () => {
            try {
                const data = await getAgentStatus(job.job_id);
                setJob(data);
                if (data.result) setResult(data.result);
                if (['PASSED', 'FAILED', 'PARTIAL', 'ERROR', 'COMPLETE'].includes(data.status)) {
                    clearInterval(pollRef.current);
                    setLoading(false);
                }
                // Also check result.status for completion
                if (data.result?.status && ['PASSED', 'FAILED'].includes(data.result.status)) {
                    clearInterval(pollRef.current);
                    setLoading(false);
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }, 2500);

        return () => clearInterval(pollRef.current);
    }, [job?.job_id, job?.status]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!form.repo_url.trim() || !form.team_name.trim() || !form.leader_name.trim()) {
            setError('All fields are required.');
            return;
        }
        setError('');
        setLoading(true);
        setJob(null);
        setResult(null);

        try {
            const data = await startHealingAgent(form);
            setJob({ job_id: data.job_id, status: data.status, ...form });
        } catch (err) {
            setError(err.message || 'Failed to start agent.');
            setLoading(false);
        }
    };

    const handleReset = () => {
        clearInterval(pollRef.current);
        setJob(null);
        setResult(null);
        setError('');
        setLoading(false);
    };

    const branchPreview = form.team_name || form.leader_name
        ? `${form.team_name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_${form.leader_name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_AI_Fix`
        : null;

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-950 via-indigo-950 to-slate-950 relative overflow-hidden">
            {/* Background decoration */}
            <div className="absolute inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-40 -right-40 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl" />
                <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-purple-500/10 rounded-full blur-3xl" />
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-blue-500/5 rounded-full blur-3xl" />
            </div>

            <div className="relative z-10 max-w-5xl mx-auto px-4 py-8 space-y-8">
                {/* Header */}
                <div className="text-center space-y-2">
                    <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 text-sm font-medium mb-2">
                        <Activity size={14} />
                        Autonomous CI/CD Healing
                    </div>
                    <h1 className="text-4xl md:text-5xl font-black bg-gradient-to-r from-white via-indigo-200 to-purple-200 bg-clip-text text-transparent">
                        AI Repair Agent
                    </h1>
                    <p className="text-slate-400 text-lg max-w-xl mx-auto">
                        Drop in a buggy repository. The agent clones it, runs tests in Docker, classifies failures, and applies LLM-generated fixes automatically.
                    </p>
                </div>

                {/* Input Form */}
                <div className="rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm p-6">
                    <h2 className="text-xl font-bold text-white mb-5 flex items-center gap-2">
                        <Terminal size={20} className="text-indigo-400" /> Configure Agent Run
                    </h2>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div className="grid md:grid-cols-2 gap-4">
                            <div className="md:col-span-2">
                                <label className="block text-sm font-medium text-slate-300 mb-1.5">GitHub Repository URL</label>
                                <div className="relative">
                                    <Code2 size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                    <input
                                        type="url"
                                        id="repo-url-input"
                                        placeholder="https://github.com/owner/repo"
                                        value={form.repo_url}
                                        onChange={(e) => setForm(f => ({ ...f, repo_url: e.target.value }))}
                                        className="w-full pl-9 pr-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 focus:bg-white/8 transition-all"
                                        disabled={loading}
                                    />
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-1.5">Team Name</label>
                                <input
                                    type="text"
                                    id="team-name-input"
                                    placeholder="e.g. RIFT ORGANISERS"
                                    value={form.team_name}
                                    onChange={(e) => setForm(f => ({ ...f, team_name: e.target.value }))}
                                    className="w-full px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 transition-all"
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-1.5">Team Leader Name</label>
                                <input
                                    type="text"
                                    id="leader-name-input"
                                    placeholder="e.g. Saiyam Kumar"
                                    value={form.leader_name}
                                    onChange={(e) => setForm(f => ({ ...f, leader_name: e.target.value }))}
                                    className="w-full px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 transition-all"
                                    disabled={loading}
                                />
                            </div>
                        </div>

                        {/* Branch preview */}
                        {branchPreview && (
                            <div className="flex items-center gap-2 text-xs text-slate-400 bg-white/5 rounded-xl px-4 py-2.5 border border-white/5">
                                <GitBranch size={13} className="text-indigo-400 shrink-0" />
                                Branch will be: <span className="font-mono text-indigo-300 ml-1">{branchPreview}</span>
                            </div>
                        )}

                        {/* Max retries */}
                        <div className="flex items-center gap-3">
                            <label className="text-sm font-medium text-slate-300 shrink-0">Max Retries:</label>
                            <input
                                type="number"
                                id="retry-limit-input"
                                min={1} max={10}
                                value={form.retry_limit}
                                onChange={(e) => setForm(f => ({ ...f, retry_limit: parseInt(e.target.value) || 5 }))}
                                className="w-20 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white text-center focus:outline-none focus:border-indigo-500/50 transition-all"
                                disabled={loading}
                            />
                        </div>

                        {error && (
                            <div className="flex items-center gap-2 text-red-300 text-sm bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3">
                                <XCircle size={16} /> {error}
                            </div>
                        )}

                        <div className="flex gap-3">
                            <button
                                type="submit"
                                id="run-agent-btn"
                                disabled={loading}
                                className="flex-1 flex items-center justify-center gap-2 py-3.5 rounded-xl font-bold text-white bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-400 hover:to-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/30 hover:scale-[1.01]"
                            >
                                {loading ? (
                                    <>
                                        <Loader2 size={18} className="animate-spin" />
                                        Agent Running...
                                    </>
                                ) : (
                                    <>
                                        <Play size={18} />
                                        Run Healing Agent
                                    </>
                                )}
                            </button>
                            {(job || result) && (
                                <button
                                    type="button"
                                    onClick={handleReset}
                                    className="px-4 py-3.5 rounded-xl border border-white/10 text-slate-300 hover:bg-white/5 transition-all flex items-center gap-2"
                                >
                                    <RotateCcw size={16} /> Reset
                                </button>
                            )}
                        </div>
                    </form>
                </div>

                {/* Loading / Agent Status */}
                {job && (
                    <div className="space-y-6">
                        {/* CI/CD Timeline */}
                        <CITimeline
                            timeline={result?.cicd_timeline}
                            maxRetries={result?.max_retries || form.retry_limit}
                        />

                        {/* Summary grid */}
                        <div className="grid md:grid-cols-2 gap-6">
                            <RunSummaryCard job={job} result={result} />
                            <ScoreBoard result={result} />
                        </div>

                        {/* Fixes Table */}
                        <FixesTable fixes={result?.fixes} />

                        {/* Error message if any */}
                        {result?.error && (
                            <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-4 text-red-300 text-sm">
                                <strong>Error:</strong> {result.error}
                            </div>
                        )}
                    </div>
                )}

                {/* Empty state when no job */}
                {!job && (
                    <div className="text-center py-12 opacity-50">
                        <Bug size={48} className="mx-auto mb-3 text-indigo-400" />
                        <p className="text-slate-400">Submit a repository above to begin autonomous healing.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
