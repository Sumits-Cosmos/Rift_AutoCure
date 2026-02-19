import { ArrowRight, GitBranch, Bug, Zap, Shield, BarChart3 } from 'lucide-react';

/**
 * Home Page — Dark hero landing with animated elements
 */
const HomePage = ({ setPage }) => {
  const features = [
    { icon: GitBranch, title: 'Auto Clone & Analyze', desc: 'Clones your repo and maps out every test file automatically.' },
    { icon: Bug, title: 'Smart Bug Detection', desc: 'Classifies failures into SYNTAX, LOGIC, LINTING, IMPORT, and more.' },
    { icon: Zap, title: 'LLM-Powered Fixes', desc: 'Generates precise, targeted code fixes using AI agents.' },
    { icon: Shield, title: 'Docker Sandboxed', desc: 'Runs everything safely inside isolated Docker containers.' },
    { icon: BarChart3, title: 'Live Dashboard', desc: 'Real-time progress tracking with score breakdown and CI/CD timeline.' },
  ];

  return (
    <div className="flex-1 flex flex-col">
      {/* Hero Section */}
      <section className="flex-1 flex flex-col items-center justify-center px-4 py-20 text-center relative overflow-hidden">
        {/* Decorative grid */}
        <div className="absolute inset-0 bg-[linear-gradient(rgba(99,102,241,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(99,102,241,0.03)_1px,transparent_1px)] bg-[size:60px_60px] pointer-events-none" />

        {/* Badge */}
        <div className="animate-fadeInUp inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 text-sm font-medium mb-6">
          <Zap size={14} className="text-indigo-400" />
          Autonomous DevOps Agent
        </div>

        {/* Heading */}
        <h1 className="animate-fadeInUp stagger-1 text-5xl md:text-7xl font-black leading-tight max-w-4xl mb-6" style={{ opacity: 0 }}>
          <span className="gradient-text">CI/CD Healing</span>
          <br />
          <span className="text-white">Powered by AI Agents</span>
        </h1>

        {/* Subtitle */}
        <p className="animate-fadeInUp stagger-2 text-lg md:text-xl text-slate-400 mb-10 max-w-2xl leading-relaxed" style={{ opacity: 0 }}>
          Drop in a buggy repository. Our AI clones it, discovers tests, classifies failures,
          generates fixes, and pushes them — fully autonomous, zero human intervention.
        </p>

        {/* CTA Buttons */}
        <div className="animate-fadeInUp stagger-3 flex flex-col sm:flex-row gap-4" style={{ opacity: 0 }}>
          <button
            onClick={() => setPage('healing')}
            className="btn-primary px-8 py-4 text-lg flex items-center gap-2 group"
          >
            Launch Healing Agent
            <ArrowRight size={18} className="group-hover:translate-x-1 transition-transform" />
          </button>
          <button
            onClick={() => setPage('testing')}
            className="px-8 py-4 text-lg font-semibold rounded-xl bg-white/[0.06] text-white border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all duration-200"
          >
            API Testing
          </button>
        </div>
      </section>

      {/* Features Grid */}
      <section className="max-w-6xl mx-auto px-4 pb-20 w-full">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {features.map((f, i) => {
            const Icon = f.icon;
            return (
              <div
                key={i}
                className="glass-card glass-card-hover p-6 group cursor-default animate-fadeInUp"
                style={{ opacity: 0, animationDelay: `${0.4 + i * 0.1}s` }}
              >
                <div className="w-10 h-10 rounded-lg bg-indigo-500/15 flex items-center justify-center mb-4 group-hover:bg-indigo-500/25 transition-colors">
                  <Icon size={20} className="text-indigo-400" />
                </div>
                <h3 className="text-white font-bold text-lg mb-2">{f.title}</h3>
                <p className="text-slate-400 text-sm leading-relaxed">{f.desc}</p>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
};

export default HomePage;
