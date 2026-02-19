import { useState, useEffect, useRef } from 'react';
import {
    GitBranch, Play, Clock, CheckCircle2, XCircle, AlertCircle,
    Loader2, Bug, Wrench, Timer, Trophy, RotateCcw,
    Code2, Terminal, Activity, ArrowUpRight, MonitorUp
} from 'lucide-react';
import { startHealingAgent, getAgentStatus, getAgentLogs } from '../../services/healingService';

// ─── Status helpers ───────────────────────────────────────────────────────────
const STATUS_CONFIG = {
    QUEUED: { icon: Clock, color: 'text-amber-400', bg: 'bg-amber-400/10', border: 'border-amber-400/30', label: 'Queued' },
    RUNNING: { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-400/10', border: 'border-blue-400/30', label: 'Running', spin: true },
    PASSED: { icon: CheckCircle2, color: 'text-emerald-400', bg: 'bg-emerald-400/10', border: 'border-emerald-400/30', label: 'Passed' },
    PARTIAL: { icon: AlertCircle, color: 'text-amber-400', bg: 'bg-amber-400/10', border: 'border-amber-400/30', label: 'Partial' },
    FAILED: { icon: XCircle, color: 'text-rose-400', bg: 'bg-rose-400/10', border: 'border-rose-400/30', label: 'Failed' },
    ERROR: { icon: XCircle, color: 'text-rose-500', bg: 'bg-rose-500/10', border: 'border-rose-500/30', label: 'Error' },
};

const BUG_TYPE_COLORS = {
    DEPENDENCY: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30',
    STRUCTURAL: 'bg-pink-500/15 text-pink-300 border-pink-500/30',
    SYNTAX: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    LOGIC: 'bg-purple-500/15 text-purple-300 border-purple-500/30',
    TYPE_ERROR: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
    IMPORT: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
    LINTING: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    INDENTATION: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
};

function calcScore(result) {
    if (!result) return null;
    let score = 100;
    const timeMins = (result.time_taken_seconds || 0) / 60;
    if (timeMins < 5) score += 10;
    const commits = result.total_fixes || 0;
    if (commits > 20) score -= (commits - 20) * 2;
    return Math.max(0, score);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }) {
    const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.QUEUED;
    const Icon = cfg.icon;
    return (
        <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold border ${cfg.bg} ${cfg.color} ${cfg.border}`}>
            <Icon size={13} className={cfg.spin ? 'animate-spin' : ''} />
            {cfg.label}
        </span>
    );
}

// ─── Full-width Progress Header ──────────────────────────────────────────────

function ProgressHeader({ iterations, maxIterations = 5, status, timestamps, isRunning }) {
    const progress = maxIterations > 0 ? (iterations / maxIterations) * 100 : 0;
    const isFinal = ['PASSED', 'FAILED', 'PARTIAL', 'ERROR'].includes(status);
    const isPassed = status === 'PASSED';

    const elapsed = timestamps?.start
        ? ((timestamps?.end || Date.now() / 1000) - timestamps.start).toFixed(0)
        : null;

    return (
        <div className="glass-card p-5 animate-fadeInUp" role="region" aria-label="Healing progress">
            {/* Top row: status info */}
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${isRunning ? 'bg-blue-500/15 border border-blue-500/30' :
                        isPassed ? 'bg-emerald-500/15 border border-emerald-500/30' :
                            isFinal ? 'bg-rose-500/15 border border-rose-500/30' :
                                'bg-indigo-500/15 border border-indigo-500/30'
                        }`}>
                        {isRunning ? (
                            <Loader2 size={18} className="text-blue-400 animate-spin" />
                        ) : isPassed ? (
                            <CheckCircle2 size={18} className="text-emerald-400" />
                        ) : isFinal ? (
                            <XCircle size={18} className="text-rose-400" />
                        ) : (
                            <Activity size={18} className="text-indigo-400" />
                        )}
                    </div>
                    <div>
                        <h2 className="text-white font-bold text-base">
                            {isRunning ? 'Agent Running' : isPassed ? 'Pipeline Healed' : isFinal ? 'Run Complete' : 'Healing Progress'}
                        </h2>
                        <p className="text-xs text-slate-500">
                            Iteration {iterations}/{maxIterations}
                            {elapsed && <span className="ml-3 text-slate-600">⏱ {elapsed}s elapsed</span>}
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    {timestamps?.start && (
                        <span className="text-xs text-slate-500 flex items-center gap-1 bg-white/[0.03] px-2.5 py-1 rounded-lg">
                            <Clock size={11} />
                            {new Date(timestamps.start * 1000).toLocaleTimeString()}
                        </span>
                    )}
                    <StatusBadge status={status} />
                </div>
            </div>

            {/* Progress bar — full width */}
            <div
                className="w-full h-2.5 bg-white/[0.05] rounded-full overflow-hidden relative"
                role="progressbar"
                aria-valuenow={iterations}
                aria-valuemin={0}
                aria-valuemax={maxIterations}
                aria-label={`Progress: ${iterations} of ${maxIterations} iterations`}
            >
                <div
                    className={`h-full rounded-full transition-all duration-700 ease-out relative overflow-hidden ${isPassed
                        ? 'bg-gradient-to-r from-emerald-500 to-emerald-400'
                        : isFinal
                            ? 'bg-gradient-to-r from-rose-500 to-rose-400'
                            : 'bg-gradient-to-r from-indigo-500 via-purple-500 to-indigo-400'
                        }`}
                    style={{ width: `${isFinal && !isPassed ? 100 : Math.max(progress, 5)}%` }}
                >
                    {/* Shimmer overlay */}
                    {isRunning && (
                        <div
                            className="absolute inset-0 w-1/2"
                            style={{
                                background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.25), transparent)',
                                animation: 'progressShimmer 2s ease-in-out infinite',
                            }}
                        />
                    )}
                </div>
            </div>

            {/* Iteration dots */}
            <div className="flex items-center gap-1 mt-3">
                {Array.from({ length: maxIterations }).map((_, i) => {
                    const done = i + 1 <= iterations;
                    const current = i + 1 === iterations && isRunning;
                    return (
                        <div key={i} className="flex items-center flex-1 gap-1">
                            <div
                                className={`w-2 h-2 rounded-full transition-all duration-500 shrink-0 ${current
                                    ? 'bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,0.6)] scale-125'
                                    : done
                                        ? 'bg-emerald-400/70'
                                        : 'bg-white/[0.08]'
                                    }`}
                            />
                            {i < maxIterations - 1 && (
                                <div className={`flex-1 h-px ${done ? 'bg-emerald-500/30' : 'bg-white/[0.05]'}`} />
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ─── Run Summary Card ─────────────────────────────────────────────────────────

function RunSummaryCard({ job, result }) {
    if (!job) return null;
    const duration = result?.time_taken_seconds;
    const isFinalPassed = job.status === 'PASSED';
    const isFinalFailed = ['FAILED', 'ERROR'].includes(job.status);

    return (
        <div className="glass-card p-5 space-y-4 animate-fadeInUp">
            <div className="flex items-center justify-between">
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                    <Activity size={16} className="text-indigo-400" />
                    Run Summary
                </h3>
                <StatusBadge status={job.status} />
            </div>

            {/* Final CI/CD Status Badge */}
            {(isFinalPassed || isFinalFailed) && (
                <div className={`flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold ${isFinalPassed
                    ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
                    : 'bg-rose-500/10 border border-rose-500/30 text-rose-400'
                    }`}>
                    {isFinalPassed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                    Final CI/CD: {isFinalPassed ? 'PASSED' : 'FAILED'}
                </div>
            )}

            <div className="grid grid-cols-2 gap-2.5 text-sm">
                <InfoRow label="Repository" value={job.repo_url} truncate />
                <InfoRow label="Team" value={job.team_name} />
                <InfoRow label="Leader" value={job.leader_name} />
                <InfoRow label="Branch" value={result?.branch || '—'} mono />
                <InfoRow label="Language" value={result?.language || '—'} />
                <InfoRow label="Framework" value={result?.test_framework || '—'} />
                <InfoRow label="Failures" value={result?.total_failures ?? '—'} highlight={result?.total_failures > 0 ? 'rose' : null} />
                <InfoRow label="Fixes" value={result?.total_fixes ?? '—'} highlight={result?.total_fixes > 0 ? 'emerald' : null} />
                <InfoRow label="Iterations" value={result ? `${result.iterations_used}/${5}` : '—'} />
                <InfoRow label="Time" value={duration ? `${duration.toFixed(1)}s` : '—'} />
                {result?.deployment_url && (
                    <div className="col-span-2 bg-indigo-500/10 border border-indigo-500/20 rounded-xl p-3 flex items-center justify-between">
                        <div>
                            <p className="text-xs text-indigo-300 mb-0.5">🚀 Deployed App</p>
                            <a href={result.deployment_url} target="_blank" rel="noopener noreferrer"
                                className="text-sm font-bold text-white hover:text-indigo-300 transition-colors truncate block">
                                {result.deployment_url}
                            </a>
                        </div>
                        <ArrowUpRight size={16} className="text-indigo-400 shrink-0" />
                    </div>
                )}
                {result?.git_push_status && (
                    <div className="col-span-2 bg-white/[0.03] rounded-xl p-3">
                        <p className="text-xs text-slate-500 mb-0.5">Git Push Status</p>
                        <p className="text-sm font-mono text-white truncate">{result.git_push_status}</p>
                    </div>
                )}
            </div>
        </div>
    );
}

function InfoRow({ label, value, mono, truncate, highlight }) {
    let highlightClass = '';
    if (highlight === 'rose') highlightClass = 'text-rose-400';
    else if (highlight === 'emerald') highlightClass = 'text-emerald-400';

    return (
        <div className="bg-white/[0.03] rounded-xl p-2.5 min-w-0 hover:bg-white/[0.05] transition-colors group">
            <p className="text-[11px] text-slate-500 mb-0.5 group-hover:text-slate-400 transition-colors">{label}</p>
            <p className={`text-sm font-semibold ${highlightClass || 'text-white'} ${mono ? 'font-mono text-xs' : ''} ${truncate ? 'truncate' : ''}`}>
                {String(value)}
            </p>
        </div>
    );
}

// ─── Fixes Table ──────────────────────────────────────────────────────────────

function FixesTable({ fixes }) {
    if (!fixes || fixes.length === 0) {
        return (
            <div className="glass-card p-6 text-center animate-fadeInUp">
                <Wrench size={28} className="mx-auto mb-2 text-slate-600" />
                <p className="text-sm text-slate-500">No fixes applied yet.</p>
            </div>
        );
    }
    return (
        <div className="glass-card overflow-hidden animate-fadeInUp">
            <div className="px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-2">
                <Wrench size={16} className="text-indigo-400" />
                <h3 className="text-base font-bold text-white">Applied Fixes</h3>
                <span className="ml-auto text-xs bg-indigo-500/15 text-indigo-300 px-2.5 py-1 rounded-full border border-indigo-500/25 font-bold">
                    {fixes.length} fix{fixes.length !== 1 ? 'es' : ''}
                </span>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm" role="table" aria-label="Applied fixes">
                    <thead>
                        <tr className="border-b border-white/[0.06] text-slate-500 text-xs uppercase tracking-wider">
                            <th className="px-5 py-3 text-left" scope="col">File</th>
                            <th className="px-5 py-3 text-left" scope="col">Bug Type</th>
                            <th className="px-5 py-3 text-left" scope="col">Line</th>
                            <th className="px-5 py-3 text-left" scope="col">Commit Message</th>
                            <th className="px-5 py-3 text-left" scope="col">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {fixes.map((fix, i) => (
                            <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors">
                                <td className="px-5 py-3 font-mono text-slate-300 text-xs">
                                    <span className="bg-white/[0.05] px-2 py-1 rounded">{fix.file}</span>
                                </td>
                                <td className="px-5 py-3">
                                    <span className={`text-xs px-2.5 py-1 rounded-full border font-bold ${BUG_TYPE_COLORS[fix.bug_type] || 'bg-slate-500/15 text-slate-300 border-slate-500/30'}`}>
                                        {fix.bug_type}
                                    </span>
                                </td>
                                <td className="px-5 py-3 text-slate-300 font-mono text-xs">{fix.line}</td>
                                <td className="px-5 py-3 text-slate-400 text-xs max-w-xs truncate" title={fix.commit_message}>
                                    {fix.commit_message}
                                </td>
                                <td className="px-5 py-3">
                                    {fix.status === 'failed' ? (
                                        <span className="text-xs px-2.5 py-1 rounded-full bg-rose-500/15 text-rose-300 border border-rose-500/25 font-bold flex items-center gap-1 w-fit">
                                            <XCircle size={12} /> Failed
                                        </span>
                                    ) : (
                                        <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/25 font-bold flex items-center gap-1 w-fit">
                                            <CheckCircle2 size={12} /> Fixed
                                        </span>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// ─── Score Board ──────────────────────────────────────────────────────────────

function ScoreBoard({ result }) {
    if (!result) return null;
    const score = calcScore(result);
    const timeMins = (result.time_taken_seconds || 0) / 60;
    const speedBonus = timeMins < 5 ? 10 : 0;
    const commitPenalty = result.total_fixes > 20 ? (result.total_fixes - 20) * 2 : 0;
    const maxScore = 110;
    const percentage = Math.min(100, Math.round((score / maxScore) * 100));

    return (
        <div className="glass-card p-5 animate-fadeInUp relative overflow-hidden">
            {/* Decorative gradients */}
            <div className="absolute -top-16 -right-16 w-32 h-32 bg-indigo-500/[0.08] rounded-full blur-[50px] pointer-events-none" />
            <div className="absolute -bottom-16 -left-16 w-32 h-32 bg-purple-500/[0.08] rounded-full blur-[50px] pointer-events-none" />

            <div className="relative z-10">
                <div className="flex items-center gap-2 mb-4">
                    <Trophy size={16} className="text-amber-400" />
                    <h3 className="text-base font-bold text-white">Score Breakdown</h3>
                </div>

                {/* Score display */}
                <div className="flex items-center justify-center mb-5">
                    <div className="relative w-28 h-28">
                        <svg className="w-28 h-28 -rotate-90" viewBox="0 0 120 120" aria-hidden="true">
                            <circle cx="60" cy="60" r="52" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="8" />
                            <circle
                                cx="60" cy="60" r="52" fill="none"
                                stroke={score >= 100 ? '#34d399' : score >= 80 ? '#fbbf24' : '#fb7185'}
                                strokeWidth="8"
                                strokeLinecap="round"
                                strokeDasharray={`${percentage * 3.27} ${327 - percentage * 3.27}`}
                                className="transition-all duration-1000 ease-out"
                            />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className={`text-2xl font-black ${score >= 100 ? 'text-emerald-400' : score >= 80 ? 'text-amber-400' : 'text-rose-400'}`}>
                                {score}
                            </span>
                            <span className="text-[10px] text-slate-500 font-medium">POINTS</span>
                        </div>
                    </div>
                </div>

                {/* Breakdown */}
                <div className="space-y-2">
                    <ScoreLine label="Base Score" value="+100" color="text-slate-300" />
                    <ScoreLine
                        label={`Speed (${timeMins.toFixed(1)} min)`}
                        value={speedBonus > 0 ? `+${speedBonus}` : '—'}
                        color={speedBonus > 0 ? 'text-emerald-400' : 'text-slate-600'}
                    />
                    <ScoreLine
                        label={`Commits (${result.total_fixes})`}
                        value={commitPenalty > 0 ? `-${commitPenalty}` : '—'}
                        color={commitPenalty > 0 ? 'text-rose-400' : 'text-slate-600'}
                    />
                    <div className="border-t border-white/[0.06] pt-2 mt-2">
                        <ScoreLine label="Final Score" value={score} color={score >= 100 ? 'text-emerald-400' : 'text-amber-400'} bold />
                    </div>
                </div>
            </div>
        </div>
    );
}

function ScoreLine({ label, value, color, bold }) {
    return (
        <div className="flex justify-between items-center py-0.5">
            <span className={`text-slate-400 text-sm ${bold ? 'font-bold text-white' : ''}`}>{label}</span>
            <span className={`font-bold ${color} ${bold ? 'text-lg' : 'text-sm'}`}>{value}</span>
        </div>
    );
}

// ─── Agent Terminal (Live Log Output) ─────────────────────────────────────────

function AgentTerminal({ logs, isRunning }) {
    const scrollRef = useRef(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs]);

    const levelColors = {
        info: 'text-emerald-400',
        warn: 'text-amber-400',
        error: 'text-rose-400',
    };

    const levelPrefix = {
        info: '$',
        warn: '⚠',
        error: '✗',
    };

    return (
        <div className="glass-card overflow-hidden animate-fadeInUp flex flex-col sticky top-20" style={{ maxHeight: 'calc(100vh - 7rem)' }}>
            {/* Terminal titlebar */}
            <div className="flex items-center gap-2 px-4 py-2.5 bg-white/[0.04] border-b border-white/[0.06] shrink-0">
                <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-rose-500/80" />
                    <div className="w-3 h-3 rounded-full bg-amber-500/80" />
                    <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
                </div>
                <div className="flex items-center gap-2 ml-2">
                    <MonitorUp size={13} className="text-indigo-400" />
                    <span className="text-sm font-bold text-white">Agent Terminal</span>
                </div>
                {isRunning && (
                    <span className="ml-auto flex items-center gap-1.5 text-xs text-blue-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                        LIVE
                    </span>
                )}
            </div>

            {/* Terminal body */}
            <div
                ref={scrollRef}
                className="p-4 font-mono text-[13px] leading-6 overflow-y-auto bg-[#0c0e18] flex-1"
                role="log"
                aria-live="polite"
                aria-label="Agent output log"
                style={{ minHeight: '360px' }}
            >
                {logs.length === 0 ? (
                    <div className="text-slate-600 flex items-center gap-2">
                        <Loader2 size={14} className="animate-spin" />
                        Waiting for agent output...
                    </div>
                ) : (
                    logs.map((log, i) => {
                        const ts = new Date(log.ts * 1000).toLocaleTimeString('en-US', { hour12: false });
                        const color = levelColors[log.level] || 'text-slate-300';
                        const prefix = levelPrefix[log.level] || '$';
                        return (
                            <div key={i} className="flex gap-3 hover:bg-white/[0.02] px-2 -mx-2 rounded">
                                <span className="text-slate-600 shrink-0 select-none">{ts}</span>
                                <span className={`${color} shrink-0 select-none w-3 text-center`}>{prefix}</span>
                                <span className={color}>{log.message}</span>
                            </div>
                        );
                    })
                )}
                {isRunning && (
                    <div className="flex items-center gap-2 mt-1 text-indigo-400">
                        <span className="animate-pulse">▌</span>
                    </div>
                )}
            </div>
        </div>
    );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function HealingAgentPage() {
    const [form, setForm] = useState({ repo_url: '', team_name: '', leader_name: '', retry_limit: 5, pat_token: '' });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [job, setJob] = useState(null);
    const [result, setResult] = useState(null);
    const [logs, setLogs] = useState([]);
    const pollRef = useRef(null);
    const logPollRef = useRef(null);
    const lastLogTs = useRef(0);

    // Start polling when we have a job
    useEffect(() => {
        if (!job?.job_id) return;
        if (['PASSED', 'FAILED', 'PARTIAL', 'ERROR'].includes(job.status)) return;

        pollRef.current = setInterval(async () => {
            try {
                const data = await getAgentStatus(job.job_id);
                setJob(data);
                if (data.result) setResult(data.result);
                if (['PASSED', 'FAILED', 'PARTIAL', 'ERROR'].includes(data.status)) {
                    clearInterval(pollRef.current);
                    setLoading(false);
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }, 2500);

        return () => clearInterval(pollRef.current);
    }, [job?.job_id, job?.status]);

    // Poll agent logs
    useEffect(() => {
        if (!job?.job_id) return;
        if (['PASSED', 'FAILED', 'PARTIAL', 'ERROR'].includes(job.status) && logs.length > 0) return;

        const fetchLogs = async () => {
            try {
                const data = await getAgentLogs(job.job_id, lastLogTs.current);
                if (data.logs && data.logs.length > 0) {
                    setLogs(prev => [...prev, ...data.logs]);
                    lastLogTs.current = data.logs[data.logs.length - 1].ts;
                }
            } catch (e) {
                // silent — logs are optional
            }
        };

        fetchLogs(); // immediate first fetch
        logPollRef.current = setInterval(fetchLogs, 1500);

        return () => clearInterval(logPollRef.current);
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
        setLogs([]);
        lastLogTs.current = 0;

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
        clearInterval(logPollRef.current);
        setJob(null);
        setResult(null);
        setLogs([]);
        setError('');
        setLoading(false);
        lastLogTs.current = 0;
    };

    const branchPreview = form.team_name || form.leader_name
        ? `${form.team_name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_${form.leader_name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_AI_Fix`
        : null;

    return (
        <div className="min-h-screen relative">
            <div className="relative z-10 max-w-7xl mx-auto px-4 py-6 space-y-5">
                {/* Header */}
                <div className="text-center space-y-2 animate-fadeInUp">
                    <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 text-sm font-medium">
                        <Activity size={14} />
                        <span>Autonomous CI/CD Healing</span>
                    </div>
                    <h1 className="text-4xl md:text-5xl font-black gradient-text">
                        AI Repair Agent
                    </h1>
                    <p className="text-slate-400 text-base max-w-xl mx-auto">
                        Drop in a buggy repository. The agent clones it, runs tests in Docker, classifies failures, and applies LLM-generated fixes automatically.
                    </p>
                </div>

                {/* ─── Input Section ──────────────────────────────────────── */}
                <div className="glass-card p-5 animate-fadeInUp stagger-1" style={{ opacity: 0 }}>
                    <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                        <Terminal size={18} className="text-indigo-400" />
                        Configure Agent Run
                    </h2>
                    <form onSubmit={handleSubmit} className="space-y-3.5">
                        <div className="grid md:grid-cols-2 gap-3.5">
                            <div className="md:col-span-2">
                                <label htmlFor="repo-url-input" className="block text-sm font-medium text-slate-300 mb-1.5">GitHub Repository URL</label>
                                <div className="relative">
                                    <Code2 size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                                    <input
                                        type="url"
                                        id="repo-url-input"
                                        placeholder="https://github.com/owner/repo"
                                        value={form.repo_url}
                                        onChange={(e) => setForm(f => ({ ...f, repo_url: e.target.value }))}
                                        className="input-dark w-full pl-10 pr-4 py-3"
                                        disabled={loading}
                                    />
                                </div>
                            </div>
                            <div>
                                <label htmlFor="team-name-input" className="block text-sm font-medium text-slate-300 mb-1.5">Team Name</label>
                                <input
                                    type="text"
                                    id="team-name-input"
                                    placeholder='e.g. "RIFT ORGANISERS"'
                                    value={form.team_name}
                                    onChange={(e) => setForm(f => ({ ...f, team_name: e.target.value }))}
                                    className="input-dark w-full px-4 py-3"
                                    disabled={loading}
                                />
                            </div>
                            <div>
                                <label htmlFor="leader-name-input" className="block text-sm font-medium text-slate-300 mb-1.5">Team Leader Name</label>
                                <input
                                    type="text"
                                    id="leader-name-input"
                                    placeholder='e.g. "Saiyam Kumar"'
                                    value={form.leader_name}
                                    onChange={(e) => setForm(f => ({ ...f, leader_name: e.target.value }))}
                                    className="input-dark w-full px-4 py-3"
                                    disabled={loading}
                                />
                            </div>
                            <div className="md:col-span-2">
                                <label htmlFor="pat-token-input" className="block text-sm font-medium text-slate-300 mb-1.5 flex items-center gap-2">
                                    GitHub Personal Access Token (PAT)
                                    <span className="text-xs text-slate-500 font-normal bg-white/[0.05] px-2 py-0.5 rounded-full">Optional — for direct push</span>
                                </label>
                                <input
                                    type="password"
                                    id="pat-token-input"
                                    placeholder="ghp_..."
                                    value={form.pat_token}
                                    onChange={(e) => setForm(f => ({ ...f, pat_token: e.target.value }))}
                                    className="input-dark w-full px-4 py-3"
                                    disabled={loading}
                                />
                                <p className="text-xs text-slate-500 mt-1.5">
                                    Provide a PAT with <code>repo</code> scope to allow the agent to squash fixes and push directly to your repository.
                                </p>
                            </div>
                        </div>

                        {/* Branch preview */}
                        {branchPreview && (
                            <div className="flex items-center gap-2 text-xs text-slate-400 bg-white/[0.03] rounded-xl px-4 py-2.5 border border-white/[0.05]">
                                <GitBranch size={13} className="text-indigo-400 shrink-0" />
                                Branch: <span className="font-mono text-indigo-300 ml-1">{branchPreview}</span>
                            </div>
                        )}

                        {/* Max retries */}
                        <div className="flex items-center gap-3">
                            <label htmlFor="retry-limit-input" className="text-sm font-medium text-slate-400 shrink-0">Max Retries:</label>
                            <input
                                type="number"
                                id="retry-limit-input"
                                min={1} max={10}
                                value={form.retry_limit}
                                onChange={(e) => setForm(f => ({ ...f, retry_limit: parseInt(e.target.value) || 5 }))}
                                className="input-dark w-20 px-3 py-2 text-center"
                                disabled={loading}
                            />
                        </div>

                        {error && (
                            <div className="flex items-center gap-2 text-rose-300 text-sm bg-rose-500/10 border border-rose-500/20 rounded-xl px-4 py-3" role="alert">
                                <XCircle size={16} /> {error}
                            </div>
                        )}

                        {/* Action buttons */}
                        <div className="flex gap-3 pt-1">
                            <button
                                type="submit"
                                id="run-agent-btn"
                                disabled={loading}
                                className="btn-primary flex-1 flex items-center justify-center gap-2 py-3.5 text-base"
                                aria-label={loading ? 'Agent is currently running' : 'Run healing agent'}
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
                                    className="px-5 py-3.5 rounded-xl border border-white/10 text-slate-300 hover:bg-white/[0.05] transition-all flex items-center gap-2 text-sm font-medium"
                                    aria-label="Reset and start over"
                                >
                                    <RotateCcw size={16} /> Reset
                                </button>
                            )}
                        </div>
                    </form>
                </div>

                {/* ─── Dashboard Results Area ─────────────────────────────── */}
                {job && (
                    <div className="space-y-5">
                        {/* 1. Full-width Progress Header */}
                        <ProgressHeader
                            iterations={result?.iterations_used ?? (job.status === 'RUNNING' ? 1 : 0)}
                            maxIterations={form.retry_limit}
                            status={job.status}
                            timestamps={result?.timestamps}
                            isRunning={loading}
                        />

                        {/* 2. Two-column layout: Details (left) | Terminal (right) */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 items-start">
                            {/* Left column — Details */}
                            <div className="flex flex-col gap-5">
                                <RunSummaryCard job={job} result={result} />
                                <ScoreBoard result={result} />
                                <FixesTable fixes={result?.fixes} />
                            </div>

                            {/* Right column — Terminal */}
                            <AgentTerminal logs={logs} isRunning={loading} />
                        </div>

                        {/* Error message */}
                        {result?.error && (
                            <div className="glass-card border-rose-500/20 bg-rose-500/[0.05] p-4 text-rose-300 text-sm flex items-center gap-2" role="alert">
                                <AlertCircle size={16} className="shrink-0" />
                                <span><strong>Error:</strong> {result.error}</span>
                            </div>
                        )}
                    </div>
                )}

                {/* Empty state */}
                {!job && (
                    <div className="text-center py-14 animate-fadeIn">
                        <div className="w-18 h-18 mx-auto mb-4 rounded-2xl bg-indigo-500/10 flex items-center justify-center" style={{ width: '72px', height: '72px' }}>
                            <Bug size={32} className="text-indigo-400/60" />
                        </div>
                        <p className="text-slate-500 text-sm">Submit a repository above to begin autonomous healing.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
