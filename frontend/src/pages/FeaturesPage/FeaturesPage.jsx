import { Sparkles, ChevronRight, GitBranch, Bug, Zap, Shield, BarChart3, Cpu } from 'lucide-react';

/**
 * Features Page — Dark glassmorphic card grid
 */
const FeaturesPage = () => {
  const features = [
    { icon: Cpu, title: 'Swagger/OpenAPI Parsing', desc: 'Seamlessly import your API definitions for instant test generation. Supports both OpenAPI 3.0 and Swagger 2.0 formats.', color: 'text-indigo-400', bg: 'bg-indigo-500/15' },
    { icon: Zap, title: 'AI Test Generation', desc: 'Agents analyze your schema to create meaningful edge cases, security checks, and comprehensive functional tests.', color: 'text-amber-400', bg: 'bg-amber-500/15' },
    { icon: GitBranch, title: 'Automated Execution', desc: 'Tests are bundled and run via Newman in high-performance CI/CD friendly environments with Docker sandboxing.', color: 'text-emerald-400', bg: 'bg-emerald-500/15' },
    { icon: BarChart3, title: 'Report Generation', desc: 'Detailed execution reports with visual timelines, score breakdowns, and comprehensive fix logs.', color: 'text-purple-400', bg: 'bg-purple-500/15' },
    { icon: Bug, title: 'Smart Bug Classification', desc: 'Classifies failures into SYNTAX, LOGIC, LINTING, TYPE_ERROR, IMPORT, INDENTATION, and more for targeted fixes.', color: 'text-rose-400', bg: 'bg-rose-500/15' },
    { icon: Shield, title: 'Autonomous Healing', desc: 'LLM-powered code fixes with automatic commit, push, and CI/CD re-run — zero human intervention needed.', color: 'text-cyan-400', bg: 'bg-cyan-500/15' },
  ];

  return (
    <div className="max-w-5xl mx-auto py-12 px-6">
      <div className="text-center mb-10 animate-fadeInUp">
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 text-sm font-medium mb-4">
          <Sparkles size={14} />
          Platform Capabilities
        </div>
        <h1 className="text-4xl font-black gradient-text mb-3">Features</h1>
        <p className="text-slate-400 max-w-lg mx-auto">Everything you need for autonomous CI/CD pipeline repair and API testing.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {features.map((f, i) => {
          const Icon = f.icon;
          return (
            <div
              key={i}
              className="glass-card glass-card-hover p-6 group cursor-default animate-fadeInUp"
              style={{ opacity: 0, animationDelay: `${0.1 + i * 0.08}s` }}
            >
              <div className="flex items-start gap-4">
                <div className={`w-11 h-11 rounded-xl ${f.bg} flex items-center justify-center shrink-0 group-hover:scale-110 transition-transform`}>
                  <Icon size={22} className={f.color} />
                </div>
                <div>
                  <h3 className="text-white font-bold text-lg mb-1.5 group-hover:text-indigo-300 transition-colors">{f.title}</h3>
                  <p className="text-slate-400 text-sm leading-relaxed">{f.desc}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default FeaturesPage;
