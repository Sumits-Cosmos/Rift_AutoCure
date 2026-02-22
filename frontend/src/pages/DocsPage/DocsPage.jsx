import { BookOpen, GitBranch, TerminalSquare, ShieldCheck } from 'lucide-react';

/**
 * Documentation Page — Dark glassmorphic sections
 */
const DocsPage = () => {
  const sections = [
    {
      icon: GitBranch,
      title: 'How to Submit a Repository',
      content: 'Navigate to the Healing Agent page and enter your GitHub Repository URL. Provide your Team Name and Leader Name to correctly format the automated branch creation. The repository should contain standard tests (e.g., PyTest, Jest, Go Test) for the agent to evaluate.',
    },
    {
      icon: ShieldCheck,
      title: 'Setting the Retry Limit & Authentication',
      content: 'You can configure the Maximum Retries (default: 5) to control how many attempts the agent will make to fix failures. If you provide an optional GitHub Personal Access Token (PAT), the agent can automatically push the final squashed fixes to your repository.',
    },
    {
      icon: TerminalSquare,
      title: 'How to Monitor the Dashboard',
      content: 'Click "Run Healing Agent" to start the process. The dashboard will display a live terminal stream of the agent\'s actions, a real-time progress bar of iterations, and a detailed table of applied fixes (with diffs and explanations). Results and deployed URLs are displayed upon completion.',
    },
  ];

  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <div className="text-center mb-10 animate-fadeInUp">
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 text-sm font-medium mb-4">
          <BookOpen size={14} />
          Getting Started
        </div>
        <h1 className="text-4xl font-black gradient-text mb-3">Documentation</h1>
        <p className="text-slate-400 max-w-lg mx-auto">Learn how to use the autonomous testing and healing platform.</p>
      </div>

      <div className="space-y-4">
        {sections.map((s, i) => {
          const Icon = s.icon;
          return (
            <div
              key={i}
              className="glass-card glass-card-hover p-6 animate-fadeInUp"
              style={{ opacity: 0, animationDelay: `${0.1 + i * 0.1}s` }}
            >
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-indigo-500/15 flex items-center justify-center shrink-0">
                  <Icon size={20} className="text-indigo-400" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-white mb-2">{s.title}</h2>
                  <p className="text-slate-400 leading-relaxed">{s.content}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default DocsPage;
